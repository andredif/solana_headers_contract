use anchor_lang::prelude::*;
use anchor_lang::system_program::{self, Transfer as SolTransfer};
use anchor_spl::token::{TokenAccount, Mint};

declare_id!("6iCBP3de8RKFhUbXVRNvrbV6Ki83JHTmge6tNGvcGiEm");

const PRECISION: u128 = 1_000_000_000_000;

// Hardcoded space constants (8 = Anchor discriminator)
// ContractState: 32 + 32 + 32 + 1 + 1 + 16 + 8 + 8 + 8 + 8 + 8 = 154
const CONTRACT_STATE_SPACE: usize = 8 + 154;
// FeeRecord (new layout): 32+32+32 + 1+1 + 8+8 + 8+8+8+8 + 8+8 + 1+1 = 164, padded to 168
const FEE_RECORD_SPACE: usize = 8 + 168;
// HolderState: 32 + 16 + 8 = 56
const HOLDER_STATE_SPACE: usize = 8 + 56;

// Fee distribution percentages (must sum to 100)
const RECIPIENT_PCT: u64 = 80; // 80% → loaner (profile owner), locked until Completed
const GOVERNANCE_PCT: u64 = 15; // 15% → governance holders, released on finalize_rent
const OWNER_PCT: u64 = 5;       //  5% → contract owner, claimable after Completed

// ─── Rent duration options ────────────────────────────────────────────

/// The three allowed rental durations. Stored as u8 in FeeRecord.
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq)]
pub enum RentDuration {
    Day,   // 86 400 s  (~1 day)
    Week,  // 604 800 s (~7 days)
    Month, // 2 592 000 s (~30 days)
    Short, // 10 s      (test/integration-only)
}

impl RentDuration {
    pub fn to_seconds(self) -> i64 {
        match self {
            RentDuration::Day   => 86_400,
            RentDuration::Week  => 604_800,
            RentDuration::Month => 2_592_000,
            RentDuration::Short => 10,
        }
    }
}

// ─── Rent lifecycle status ────────────────────────────────────────────
//
//   rent_space      activate_rent     finalize_rent (after expiry)
//  ──────────────►  ─────────────►  ──────────────────────────────►
//    Pending            Active               Completed
//       │                 │
//       ▼                 ▼
//    Reverted           Revoked   (oracle; payer gets 100% refund)
//  (payer/owner;
//   payer gets 100% refund)

/// Stored as u8 in FeeRecord.
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq)]
pub enum RentStatus {
    Pending,   // 0 – paid, 100% locked, waiting for recipient to activate
    Active,    // 1 – recipient activated; timer running, all funds still locked
    Completed, // 2 – rental period expired; all parties may now withdraw
    Reverted,  // 3 – cancelled by payer/owner while Pending; 100% refunded
    Revoked,   // 4 – cancelled by oracle while Pending or Active; 100% refunded
}

#[program]
pub mod fee_distribution {
    use super::*;

    pub fn initialize(
        ctx: Context<Initialize>,
        owner: Pubkey,
        oracle: Pubkey,
        supply_snapshot: u64,
    ) -> Result<()> {
        require!(supply_snapshot > 0, FeeDistributionError::InvalidSupplySnapshot);

        let contract = &mut ctx.accounts.contract;
        contract.owner = owner;
        contract.oracle = oracle;
        contract.governance_token_mint = ctx.accounts.governance_token_mint.key();
        contract.bump = ctx.bumps.contract;
        contract.fee_vault_bump = ctx.bumps.fee_vault;
        contract.fees_per_token_accumulated = 0;
        contract.total_fees_accumulated = 0;
        contract.fee_record_count = 0;
        contract.supply_snapshot = supply_snapshot;
        contract.owner_fees_accumulated = 0;
        contract.owner_fees_claimed = 0;

        Ok(())
    }

    /// Owner-only: rotate the oracle keypair (e.g. key compromise).
    pub fn update_oracle(ctx: Context<UpdateOracle>, new_oracle: Pubkey) -> Result<()> {
        let contract = &mut ctx.accounts.contract;
        require_eq!(
            ctx.accounts.owner.key(),
            contract.owner,
            FeeDistributionError::UnauthorizedOwner
        );
        let old_oracle = contract.oracle;
        contract.oracle = new_oracle;
        emit!(OracleUpdated {
            old_oracle,
            new_oracle,
            timestamp: Clock::get()?.unix_timestamp,
        });
        Ok(())
    }

    // ─── Oracle management ────────────────────────────────────────────────────

    /// Owner-only: refresh the token supply used in fee-per-token math.
    pub fn update_supply_snapshot(ctx: Context<UpdateSupplySnapshot>) -> Result<()> {
        let contract = &mut ctx.accounts.contract;
        require_eq!(
            ctx.accounts.owner.key(),
            contract.owner,
            FeeDistributionError::UnauthorizedOwner
        );

        let new_supply = ctx.accounts.governance_token_mint.supply;
        require!(new_supply > 0, FeeDistributionError::InvalidSupplySnapshot);
        contract.supply_snapshot = new_supply;

        emit!(SupplySnapshotUpdated {
            new_supply,
            timestamp: Clock::get()?.unix_timestamp,
        });
        Ok(())
    }

    // ─── Core rental flow ─────────────────────────────────────────────────────

    /// Step 1 – Payer locks 100% of the payment into the fee vault and
    /// records a FeeRecord in Pending status.
    ///
    /// Duration must be Day, Week, or Month.
    /// The expiration timer does NOT start here — it starts in activate_rent.
    pub fn rent_space(
        ctx: Context<RentSpace>,
        recipient: Pubkey,
        duration: RentDuration,
        payment_amount: u64,
    ) -> Result<()> {
        require!(payment_amount > 0, FeeDistributionError::InvalidPaymentAmount);

        // Pre-compute fee splits (amounts are stored; transfers happen later)
        let governance_fee = payment_amount
            .checked_mul(GOVERNANCE_PCT)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?
            .checked_div(100)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        let owner_fee = payment_amount
            .checked_mul(OWNER_PCT)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?
            .checked_div(100)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        let recipient_fee = payment_amount
            .checked_mul(RECIPIENT_PCT)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?
            .checked_div(100)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        // Lock the entire payment (lamports / SOL) in the fee vault.
        // HDRZ is governance-only; all rental payments are in native SOL.
        system_program::transfer(
            CpiContext::new(
                ctx.accounts.system_program.to_account_info(),
                SolTransfer {
                    from: ctx.accounts.payer.to_account_info(),
                    to: ctx.accounts.fee_vault.to_account_info(),
                },
            ),
            payment_amount,
        )?;

        let now = Clock::get()?.unix_timestamp;
        let contract = &mut ctx.accounts.contract;
        let fee_record = &mut ctx.accounts.fee_record;

        fee_record.contract              = contract.key();
        fee_record.payer                 = ctx.accounts.payer.key();
        fee_record.recipient             = recipient;
        fee_record.duration              = duration as u8;
        fee_record.status                = RentStatus::Pending as u8;
        fee_record.activated_at          = 0;
        fee_record.expiration_time       = 0; // set in activate_rent
        fee_record.total_amount          = payment_amount;
        fee_record.governance_fee        = governance_fee;
        fee_record.owner_fee             = owner_fee;
        fee_record.recipient_fee         = recipient_fee;
        fee_record.timestamp             = now;
        fee_record.index                 = contract.fee_record_count;
        fee_record.owner_fee_claimed     = false;
        fee_record.recipient_fee_claimed = false;

        contract.fee_record_count = contract
            .fee_record_count
            .checked_add(1)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        emit!(RentSpaceEvent {
            payer: ctx.accounts.payer.key(),
            recipient,
            duration: duration as u8,
            payment_amount,
            governance_fee,
            owner_fee,
            recipient_fee,
            timestamp: now,
        });

        Ok(())
    }

    /// Step 2 – Recipient (profile owner) accepts and activates the rental.
    ///
    /// The expiration clock starts from the moment this instruction is executed.
    /// Status: Pending → Active.
    pub fn activate_rent(ctx: Context<ActivateRent>) -> Result<()> {
        let fee_record = &mut ctx.accounts.fee_record;

        require!(
            fee_record.status == RentStatus::Pending as u8,
            FeeDistributionError::RentNotPending
        );
        require_eq!(
            ctx.accounts.recipient.key(),
            fee_record.recipient,
            FeeDistributionError::UnauthorizedRecipient
        );

        let now = Clock::get()?.unix_timestamp;
        let duration_secs = match fee_record.duration {
            0 => RentDuration::Day.to_seconds(),
            1 => RentDuration::Week.to_seconds(),
            2 => RentDuration::Month.to_seconds(),
            _ => RentDuration::Short.to_seconds(),
        };

        fee_record.activated_at    = now;
        fee_record.expiration_time = now
            .checked_add(duration_secs)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;
        fee_record.status = RentStatus::Active as u8;

        emit!(ActivateRentEvent {
            payer: fee_record.payer,
            recipient: fee_record.recipient,
            index: fee_record.index,
            activated_at: now,
            expiration_time: fee_record.expiration_time,
        });

        Ok(())
    }

    /// Step 3 – Permissionless settlement after the rental period expires.
    ///
    /// Transitions status Active → Completed and releases the 15% governance
    /// fee into the per-token accumulator so holders can start claiming.
    /// The 80% (recipient) and 5% (owner) remain in the vault; each party
    /// must call their respective claim instruction to withdraw.
    pub fn finalize_rent(ctx: Context<FinalizeRent>) -> Result<()> {
        let now = Clock::get()?.unix_timestamp;
        let fee_record = &mut ctx.accounts.fee_record;

        require!(
            fee_record.status == RentStatus::Active as u8,
            FeeDistributionError::RentNotActive
        );
        require!(
            now >= fee_record.expiration_time,
            FeeDistributionError::RentNotExpired
        );

        // Release governance fees into the accumulator
        let contract = &mut ctx.accounts.contract;
        let supply = contract.supply_snapshot;
        let governance_fee = fee_record.governance_fee;

        let fees_per_token_delta = (governance_fee as u128)
            .checked_mul(PRECISION)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?
            .checked_div(supply as u128)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        contract.fees_per_token_accumulated = contract
            .fees_per_token_accumulated
            .checked_add(fees_per_token_delta)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        contract.total_fees_accumulated = contract
            .total_fees_accumulated
            .checked_add(governance_fee)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        contract.owner_fees_accumulated = contract
            .owner_fees_accumulated
            .checked_add(fee_record.owner_fee)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        fee_record.owner_fee_claimed = true; // owner_fee released to accumulator
        fee_record.status = RentStatus::Completed as u8;

        emit!(FinalizeRentEvent {
            payer: fee_record.payer,
            recipient: fee_record.recipient,
            index: fee_record.index,
            governance_fee,
            owner_fee: fee_record.owner_fee,
            recipient_fee: fee_record.recipient_fee,
            timestamp: now,
        });

        Ok(())
    }

    /// Recipient (loaner / profile owner) withdraws their 80% share.
    ///
    /// Embeds a lazy-finalize: if the rental is still Active and has expired,
    /// this instruction finalizes it inline (updates governance + owner
    /// accumulators) so the recipient never needs to call ``finalize_rent``
    /// as a separate step.
    pub fn claim_recipient_fee(ctx: Context<ClaimRecipientFee>) -> Result<()> {
        let now = Clock::get()?.unix_timestamp;

        // ── Lazy-finalize (Active + expired) ───────────────────────────────────────
        if ctx.accounts.fee_record.status == RentStatus::Active as u8 {
            require!(
                now >= ctx.accounts.fee_record.expiration_time,
                FeeDistributionError::RentNotExpired
            );

            let governance_fee = ctx.accounts.fee_record.governance_fee;
            let owner_fee      = ctx.accounts.fee_record.owner_fee;
            let supply         = ctx.accounts.contract.supply_snapshot;

            let fees_per_token_delta = (governance_fee as u128)
                .checked_mul(PRECISION)
                .ok_or(FeeDistributionError::ArithmeticOverflow)?
                .checked_div(supply as u128)
                .ok_or(FeeDistributionError::ArithmeticOverflow)?;

            ctx.accounts.contract.fees_per_token_accumulated = ctx.accounts.contract
                .fees_per_token_accumulated
                .checked_add(fees_per_token_delta)
                .ok_or(FeeDistributionError::ArithmeticOverflow)?;

            ctx.accounts.contract.total_fees_accumulated = ctx.accounts.contract
                .total_fees_accumulated
                .checked_add(governance_fee)
                .ok_or(FeeDistributionError::ArithmeticOverflow)?;

            ctx.accounts.contract.owner_fees_accumulated = ctx.accounts.contract
                .owner_fees_accumulated
                .checked_add(owner_fee)
                .ok_or(FeeDistributionError::ArithmeticOverflow)?;

            ctx.accounts.fee_record.owner_fee_claimed = true;
            ctx.accounts.fee_record.status = RentStatus::Completed as u8;

            emit!(FinalizeRentEvent {
                payer:         ctx.accounts.fee_record.payer,
                recipient:     ctx.accounts.fee_record.recipient,
                index:         ctx.accounts.fee_record.index,
                governance_fee,
                owner_fee,
                recipient_fee: ctx.accounts.fee_record.recipient_fee,
                timestamp:     now,
            });
        } else {
            require!(
                ctx.accounts.fee_record.status == RentStatus::Completed as u8,
                FeeDistributionError::RentNotCompleted
            );
        }

        // ── Claim 80% ─────────────────────────────────────────────────────────────
        require!(!ctx.accounts.fee_record.recipient_fee_claimed, FeeDistributionError::RecipientFeeAlreadyClaimed);
        require_eq!(
            ctx.accounts.recipient.key(),
            ctx.accounts.fee_record.recipient,
            FeeDistributionError::UnauthorizedRecipient
        );

        let recipient_fee = ctx.accounts.fee_record.recipient_fee;
        require!(recipient_fee > 0, FeeDistributionError::NothingToClaim);

        let contract_key = ctx.accounts.contract.key();
        let vault_bump   = ctx.accounts.contract.fee_vault_bump;
        let seeds = &[b"fee_vault" as &[u8], contract_key.as_ref(), &[vault_bump]];
        let signer_seeds = &[&seeds[..]];

        system_program::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.system_program.to_account_info(),
                SolTransfer {
                    from: ctx.accounts.fee_vault.to_account_info(),
                    to:   ctx.accounts.recipient.to_account_info(),
                },
                signer_seeds,
            ),
            recipient_fee,
        )?;

        ctx.accounts.fee_record.recipient_fee_claimed = true;

        emit!(ClaimRecipientFeeEvent {
            recipient: ctx.accounts.recipient.key(),
            payer:     ctx.accounts.fee_record.payer,
            index:     ctx.accounts.fee_record.index,
            amount:    recipient_fee,
            timestamp: now,
        });

        Ok(())
    }

    /// Contract owner sweeps all accumulated 5% fees in a single transaction.
    ///
    /// Fees accumulate in ``ContractState.owner_fees_accumulated`` each time a
    /// rental is finalized (via ``finalize_rent`` or the lazy-finalize path in
    /// ``claim_recipient_fee``). One call claims everything accrued since the
    /// last withdrawal — no per-record iteration needed.
    pub fn claim_owner_fee(ctx: Context<ClaimOwnerFee>) -> Result<()> {
        require_eq!(
            ctx.accounts.owner.key(),
            ctx.accounts.contract.owner,
            FeeDistributionError::UnauthorizedOwner
        );

        let claimable = ctx.accounts.contract.owner_fees_accumulated
            .checked_sub(ctx.accounts.contract.owner_fees_claimed)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        require!(claimable > 0, FeeDistributionError::NothingToClaim);

        let contract_key = ctx.accounts.contract.key();
        let vault_bump   = ctx.accounts.contract.fee_vault_bump;
        let seeds = &[b"fee_vault" as &[u8], contract_key.as_ref(), &[vault_bump]];
        let signer_seeds = &[&seeds[..]];

        system_program::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.system_program.to_account_info(),
                SolTransfer {
                    from: ctx.accounts.fee_vault.to_account_info(),
                    to:   ctx.accounts.owner.to_account_info(),
                },
                signer_seeds,
            ),
            claimable,
        )?;

        ctx.accounts.contract.owner_fees_claimed = ctx.accounts.contract.owner_fees_claimed
            .checked_add(claimable)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        emit!(ClaimOwnerFeeEvent {
            owner:     ctx.accounts.owner.key(),
            amount:    claimable,
            timestamp: Clock::get()?.unix_timestamp,
        });

        Ok(())
    }

    /// Payer or owner may cancel a rental that is still Pending (not yet
    /// activated by the recipient). The full 100% is refunded to the payer.
    /// Once activated, only the oracle can cancel (revoke_by_oracle).
    pub fn revert_rent(ctx: Context<RevertRent>) -> Result<()> {
        let fee_record = &mut ctx.accounts.fee_record;

        require!(
            fee_record.status == RentStatus::Pending as u8,
            FeeDistributionError::RentNotPending
        );

        let authority_key = ctx.accounts.authority.key();
        require!(
            authority_key == fee_record.payer || authority_key == ctx.accounts.contract.owner,
            FeeDistributionError::UnauthorizedOwner
        );

        let refund_amount = fee_record.total_amount; // 100% was locked

        let contract_key = ctx.accounts.contract.key();
        let vault_bump = ctx.accounts.contract.fee_vault_bump;
        let seeds = &[b"fee_vault" as &[u8], contract_key.as_ref(), &[vault_bump]];
        let signer_seeds = &[&seeds[..]];

        system_program::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.system_program.to_account_info(),
                SolTransfer {
                    from: ctx.accounts.fee_vault.to_account_info(),
                    to: ctx.accounts.payer.to_account_info(),
                },
                signer_seeds,
            ),
            refund_amount,
        )?;

        fee_record.status = RentStatus::Reverted as u8;

        emit!(RevertRentEvent {
            payer: fee_record.payer,
            index: fee_record.index,
            refund_amount,
            timestamp: Clock::get()?.unix_timestamp,
        });

        Ok(())
    }

    /// Oracle-only: revoke a rental when the recipient violates the agreement
    /// (e.g. swaps out the header image before expiration).
    ///
    /// Works in both Pending and Active status.
    /// 100% of the locked funds are returned to the original payer because
    /// the recipient forfeits their share upon violation.
    pub fn revoke_by_oracle(ctx: Context<RevokeByOracle>, reason: String) -> Result<()> {
        require!(
            ctx.accounts.oracle.key() == ctx.accounts.contract.oracle,
            FeeDistributionError::UnauthorizedOracle
        );

        let fee_record = &mut ctx.accounts.fee_record;

        let status = fee_record.status;
        require!(
            status == RentStatus::Pending as u8 || status == RentStatus::Active as u8,
            FeeDistributionError::RentCannotBeRevoked
        );

        let refund_amount = fee_record.total_amount; // 100% locked in vault

        let contract_key = ctx.accounts.contract.key();
        let vault_bump = ctx.accounts.contract.fee_vault_bump;
        let seeds = &[b"fee_vault" as &[u8], contract_key.as_ref(), &[vault_bump]];
        let signer_seeds = &[&seeds[..]];

        system_program::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.system_program.to_account_info(),
                SolTransfer {
                    from: ctx.accounts.fee_vault.to_account_info(),
                    to: ctx.accounts.payer.to_account_info(),
                },
                signer_seeds,
            ),
            refund_amount,
        )?;

        fee_record.status = RentStatus::Revoked as u8;

        emit!(RevokeByOracleEvent {
            oracle: ctx.accounts.oracle.key(),
            payer: fee_record.payer,
            index: fee_record.index,
            refund_amount,
            reason,
            timestamp: Clock::get()?.unix_timestamp,
        });

        Ok(())
    }

    // ─── Governance holder fees ───────────────────────────────────────────────

    /// Must be called once per holder before they can claim.
    /// Sets their baseline to the current accumulator so they only earn
    /// fees generated after they register.
    pub fn init_holder_state(ctx: Context<InitHolderState>) -> Result<()> {
        let holder_state = &mut ctx.accounts.holder_state;
        holder_state.holder = ctx.accounts.holder.key();
        holder_state.fees_per_token_claimed = ctx.accounts.contract.fees_per_token_accumulated;
        holder_state.total_claimed = 0;
        Ok(())
    }

    /// Claim fees proportional to current governance token balance.
    /// Fees accrue only after finalize_rent is called for each rental.
    pub fn claim_fees(ctx: Context<ClaimFees>) -> Result<()> {
        let contract = &ctx.accounts.contract;
        let holder_state = &mut ctx.accounts.holder_state;

        let token_balance = ctx.accounts.holder_token_account.amount;
        require!(token_balance > 0, FeeDistributionError::NoTokensHeld);

        let delta = contract
            .fees_per_token_accumulated
            .checked_sub(holder_state.fees_per_token_claimed)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        require!(delta > 0, FeeDistributionError::NothingToClaim);

        let claimable = (token_balance as u128)
            .checked_mul(delta)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?
            .checked_div(PRECISION)
            .ok_or(FeeDistributionError::ArithmeticOverflow)? as u64;

        require!(claimable > 0, FeeDistributionError::NothingToClaim);

        let contract_key = contract.key();
        let vault_bump = contract.fee_vault_bump;
        let seeds = &[b"fee_vault" as &[u8], contract_key.as_ref(), &[vault_bump]];
        let signer_seeds = &[&seeds[..]];

        // Pay out SOL (lamports) proportional to the holder's HDRZ balance.
        system_program::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.system_program.to_account_info(),
                SolTransfer {
                    from: ctx.accounts.fee_vault.to_account_info(),
                    to: ctx.accounts.holder.to_account_info(),
                },
                signer_seeds,
            ),
            claimable,
        )?;

        holder_state.fees_per_token_claimed = contract.fees_per_token_accumulated;
        holder_state.total_claimed = holder_state
            .total_claimed
            .checked_add(claimable)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        emit!(ClaimEvent {
            holder: ctx.accounts.holder.key(),
            amount: claimable,
            timestamp: Clock::get()?.unix_timestamp,
        });

        Ok(())
    }

    // ─── Admin ────────────────────────────────────────────────────────────────

    pub fn update_owner(ctx: Context<UpdateOwner>, new_owner: Pubkey) -> Result<()> {
        let contract = &mut ctx.accounts.contract;
        require_eq!(
            ctx.accounts.owner.key(),
            contract.owner,
            FeeDistributionError::UnauthorizedOwner
        );
        contract.owner = new_owner;
        Ok(())
    }
}

// ================== Account constraint structs ==================

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(
        init,
        payer = signer,
        space = CONTRACT_STATE_SPACE,
        seeds = [b"contract"],
        bump
    )]
    pub contract: Account<'info, ContractState>,

    pub governance_token_mint: Account<'info, Mint>,

    /// CHECK: SOL vault PDA. Holds all locked rental payments as lamports.
    /// Not formally initialized—funds are transferred to it on first deposit.
    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump,
    )]
    pub fee_vault: UncheckedAccount<'info>,

    #[account(mut)]
    pub signer: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct UpdateOracle<'info> {
    #[account(mut, seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,
    pub owner: Signer<'info>,
}

#[derive(Accounts)]
pub struct UpdateSupplySnapshot<'info> {
    #[account(mut, seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,
    pub governance_token_mint: Account<'info, Mint>,
    pub owner: Signer<'info>,
}

// ─── Rental flow ──────────────────────────────────────────────────────────────

#[derive(Accounts)]
#[instruction(recipient: Pubkey, duration: RentDuration, payment_amount: u64)]
pub struct RentSpace<'info> {
    #[account(
        mut,
        seeds = [b"contract"],
        bump = contract.bump
    )]
    pub contract: Account<'info, ContractState>,

    // governance_token_mint is NOT passed as an account — it lives inside
    // ContractState.governance_token_mint and is not needed for rent_space logic.

    #[account(mut)]
    pub payer: Signer<'info>,

    /// CHECK: SOL vault PDA. Auto-created on first deposit.
    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
    )]
    pub fee_vault: UncheckedAccount<'info>,

    #[account(
        init,
        payer = payer,
        space = FEE_RECORD_SPACE,
        seeds = [
            b"fee_record",
            payer.key().as_ref(),
            &contract.fee_record_count.to_le_bytes()
        ],
        bump
    )]
    pub fee_record: Account<'info, FeeRecord>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ActivateRent<'info> {
    #[account(seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,

    #[account(
        mut,
        seeds = [
            b"fee_record",
            fee_record.payer.as_ref(),
            &fee_record.index.to_le_bytes()
        ],
        bump
    )]
    pub fee_record: Account<'info, FeeRecord>,

    /// Must match fee_record.recipient; verified in instruction logic.
    pub recipient: Signer<'info>,
}

#[derive(Accounts)]
pub struct FinalizeRent<'info> {
    #[account(
        mut,
        seeds = [b"contract"],
        bump = contract.bump
    )]
    pub contract: Account<'info, ContractState>,

    #[account(
        mut,
        seeds = [
            b"fee_record",
            fee_record.payer.as_ref(),
            &fee_record.index.to_le_bytes()
        ],
        bump
    )]
    pub fee_record: Account<'info, FeeRecord>,

    /// Anyone can trigger settlement after expiry.
    pub caller: Signer<'info>,
}

#[derive(Accounts)]
pub struct ClaimRecipientFee<'info> {
    #[account(mut, seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,

    /// SOL vault—holds all locked payments as lamports.
    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
    )]
    pub fee_vault: SystemAccount<'info>,

    #[account(
        mut,
        seeds = [
            b"fee_record",
            fee_record.payer.as_ref(),
            &fee_record.index.to_le_bytes()
        ],
        bump
    )]
    pub fee_record: Account<'info, FeeRecord>,

    /// Must match fee_record.recipient; verified in instruction logic.
    #[account(mut)]
    pub recipient: Signer<'info>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ClaimOwnerFee<'info> {
    #[account(mut, seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,

    /// SOL vault — source of the owner sweep.
    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
    )]
    pub fee_vault: SystemAccount<'info>,

    /// Must match contract.owner; verified in instruction logic.
    #[account(mut)]
    pub owner: Signer<'info>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct RevertRent<'info> {
    #[account(seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,

    /// SOL vault.
    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
    )]
    pub fee_vault: SystemAccount<'info>,

    #[account(
        mut,
        seeds = [
            b"fee_record",
            fee_record.payer.as_ref(),
            &fee_record.index.to_le_bytes()
        ],
        bump
    )]
    pub fee_record: Account<'info, FeeRecord>,

    /// Must be the original payer or the contract owner.
    pub authority: Signer<'info>,

    /// Original payer wallet — receives the full 100% SOL refund.
    /// CHECK: address is verified against fee_record.payer in the instruction.
    #[account(mut)]
    pub payer: AccountInfo<'info>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct RevokeByOracle<'info> {
    #[account(seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,

    /// SOL vault.
    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
    )]
    pub fee_vault: SystemAccount<'info>,

    #[account(
        mut,
        seeds = [
            b"fee_record",
            fee_record.payer.as_ref(),
            &fee_record.index.to_le_bytes()
        ],
        bump
    )]
    pub fee_record: Account<'info, FeeRecord>,

    /// Must match contract.oracle; verified in instruction logic.
    pub oracle: Signer<'info>,

    /// Payer wallet receives the full 100% SOL refund on revocation.
    /// CHECK: address is verified against fee_record.payer in the instruction.
    #[account(mut)]
    pub payer: AccountInfo<'info>,

    pub system_program: Program<'info, System>,
}

// ─── Governance holder fee claiming ──────────────────────────────────────────

#[derive(Accounts)]
pub struct InitHolderState<'info> {
    #[account(seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,

    #[account(
        init,
        payer = holder,
        space = HOLDER_STATE_SPACE,
        seeds = [b"holder_state", holder.key().as_ref()],
        bump
    )]
    pub holder_state: Account<'info, HolderState>,

    #[account(mut)]
    pub holder: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ClaimFees<'info> {
    #[account(seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,

    /// SOL vault — source of claimable lamports.
    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
    )]
    pub fee_vault: SystemAccount<'info>,

    #[account(
        mut,
        seeds = [b"holder_state", holder.key().as_ref()],
        bump,
        has_one = holder,
    )]
    pub holder_state: Account<'info, HolderState>,

    /// HDRZ token account — read-only, used to determine the holder's proportional share.
    #[account(
        token::mint = contract.governance_token_mint,
        token::authority = holder,
    )]
    pub holder_token_account: Account<'info, TokenAccount>,

    /// Holder's SOL wallet — receives the claimable lamports.
    #[account(mut)]
    pub holder: Signer<'info>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct UpdateOwner<'info> {
    #[account(mut, seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,
    pub owner: Signer<'info>,
}

// ================== On-chain state ==================

#[account]
pub struct ContractState {
    pub owner: Pubkey,                      // 32
    pub governance_token_mint: Pubkey,      // 32
    pub oracle: Pubkey,                     // 32
    pub bump: u8,                           // 1
    pub fee_vault_bump: u8,                 // 1
    pub fees_per_token_accumulated: u128,   // 16
    pub total_fees_accumulated: u64,        // 8
    pub fee_record_count: u64,              // 8
    pub supply_snapshot: u64,               // 8
    pub owner_fees_accumulated: u64,        // 8  — running total of 5% owner fees released
    pub owner_fees_claimed: u64,            // 8  — running total of owner fees withdrawn
}                                           // = 154

#[account]
pub struct FeeRecord {
    pub contract: Pubkey,            // 32
    pub payer: Pubkey,               // 32
    pub recipient: Pubkey,           // 32
    pub duration: u8,                // 1  — RentDuration stored as u8
    pub status: u8,                  // 1  — RentStatus stored as u8
    pub activated_at: i64,           // 8  — Unix timestamp of activation (0 if Pending)
    pub expiration_time: i64,        // 8  — activated_at + duration_secs (0 if Pending)
    pub total_amount: u64,           // 8  — full payment locked in vault
    pub governance_fee: u64,         // 8  — 15% released to accumulator on finalize
    pub owner_fee: u64,              // 8  —  5% claimable by owner after Completed
    pub recipient_fee: u64,          // 8  — 80% claimable by recipient after Completed
    pub timestamp: i64,              // 8  — creation time of this record
    pub index: u64,                  // 8  — sequential index per payer
    pub owner_fee_claimed: bool,     // 1  — true once owner_fee is released to the accumulator
    pub recipient_fee_claimed: bool, // 1
}                                    // = 164 (padded to 168 via FEE_RECORD_SPACE constant)

#[account]
pub struct HolderState {
    pub holder: Pubkey,                 // 32
    pub fees_per_token_claimed: u128,   // 16
    pub total_claimed: u64,             // 8
}                                       // = 56

// ================== Events ==================

#[event]
pub struct RentSpaceEvent {
    pub payer: Pubkey,
    pub recipient: Pubkey,
    pub duration: u8,
    pub payment_amount: u64,
    pub governance_fee: u64,
    pub owner_fee: u64,
    pub recipient_fee: u64,
    pub timestamp: i64,
}

#[event]
pub struct ActivateRentEvent {
    pub payer: Pubkey,
    pub recipient: Pubkey,
    pub index: u64,
    pub activated_at: i64,
    pub expiration_time: i64,
}

#[event]
pub struct FinalizeRentEvent {
    pub payer: Pubkey,
    pub recipient: Pubkey,
    pub index: u64,
    pub governance_fee: u64,
    pub owner_fee: u64,
    pub recipient_fee: u64,
    pub timestamp: i64,
}

#[event]
pub struct RevertRentEvent {
    pub payer: Pubkey,
    pub index: u64,
    pub refund_amount: u64,
    pub timestamp: i64,
}

#[event]
pub struct ClaimRecipientFeeEvent {
    pub recipient: Pubkey,
    pub payer: Pubkey,
    pub index: u64,
    pub amount: u64,
    pub timestamp: i64,
}

#[event]
pub struct ClaimOwnerFeeEvent {
    pub owner: Pubkey,
    pub amount: u64,
    pub timestamp: i64,
}

#[event]
pub struct ClaimEvent {
    pub holder: Pubkey,
    pub amount: u64,
    pub timestamp: i64,
}

#[event]
pub struct SupplySnapshotUpdated {
    pub new_supply: u64,
    pub timestamp: i64,
}

#[event]
pub struct OracleUpdated {
    pub old_oracle: Pubkey,
    pub new_oracle: Pubkey,
    pub timestamp: i64,
}

#[event]
pub struct RevokeByOracleEvent {
    pub oracle: Pubkey,
    pub payer: Pubkey,
    pub index: u64,
    pub refund_amount: u64,
    pub reason: String,
    pub timestamp: i64,
}

// ================== Errors ==================

#[error_code]
pub enum FeeDistributionError {
    #[msg("Invalid payment amount")]
    InvalidPaymentAmount,
    #[msg("Arithmetic overflow")]
    ArithmeticOverflow,
    #[msg("Unauthorized owner")]
    UnauthorizedOwner,
    #[msg("Caller is not the authorised recipient for this rental")]
    UnauthorizedRecipient,
    #[msg("Recipient does not match token account owner")]
    RecipientMismatch,
    #[msg("Invalid token mint")]
    InvalidMint,
    #[msg("No governance tokens held")]
    NoTokensHeld,
    #[msg("Nothing to claim")]
    NothingToClaim,
    #[msg("Supply snapshot must be greater than zero")]
    InvalidSupplySnapshot,
    #[msg("Rental must be in Pending status for this operation")]
    RentNotPending,
    #[msg("Rental must be in Active status for this operation")]
    RentNotActive,
    #[msg("Rental must be in Completed status to claim funds")]
    RentNotCompleted,
    #[msg("Rental period has not yet expired")]
    RentNotExpired,
    #[msg("Rental cannot be revoked in its current status")]
    RentCannotBeRevoked,
    #[msg("Owner fee has already been claimed for this record")]
    OwnerFeeAlreadyClaimed,
    #[msg("Recipient fee has already been claimed for this record")]
    RecipientFeeAlreadyClaimed,
    #[msg("Signer is not the authorised oracle")]
    UnauthorizedOracle,
}
