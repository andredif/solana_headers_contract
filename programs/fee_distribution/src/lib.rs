use anchor_lang::prelude::*;
use anchor_spl::token::{self, Transfer, Token};
use anchor_spl::token::TokenAccount;
use anchor_spl::token::Mint;

declare_id!("6iCBP3de8RKFhUbXVRNvrbV6Ki83JHTmge6tNGvcGiEm");

const PRECISION: u128 = 1_000_000_000_000;

// Hardcoded space constants (8 = Anchor discriminator)
// ContractState: 32 + 32 + 32 + 1 + 1 + 16 + 8 + 8 + 8 = 138 (added oracle: Pubkey)
const CONTRACT_STATE_SPACE: usize = 8 + 138;
// FeeRecord: 32+32+32 + 8+8+8+8+8+8 + 1+1+1 = 147, padded to 152
const FEE_RECORD_SPACE: usize = 8 + 152;
// HolderState: 32 + 16 + 8 = 56
const HOLDER_STATE_SPACE: usize = 8 + 56;

// Fee distribution basis points (must sum to 100)
const RECIPIENT_PCT: u64 = 80; // 80% to renter immediately
const GOVERNANCE_PCT: u64 = 15; // 15% to governance holders (locked until expiry)
const OWNER_PCT: u64 = 5;      // 5%  to contract owner    (locked until expiry)

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
        // Snapshot supply at init time to prevent manipulation
        contract.supply_snapshot = supply_snapshot;

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

    /// Oracle-only: revoke a rent when the renter violates the agreement
    /// (e.g. swaps out the header image before expiration).
    /// The oracle backend monitors the X/Twitter profile and calls this
    /// instruction as soon as a violation is detected.
    /// All locked fees (governance 15% + owner 5%) are returned to the loaner.
    pub fn revoke_by_oracle(
        ctx: Context<RevokeByOracle>,
        reason: String,
    ) -> Result<()> {
        require!(
            ctx.accounts.oracle.key() == ctx.accounts.contract.oracle,
            FeeDistributionError::UnauthorizedOracle
        );

        let fee_record = &mut ctx.accounts.fee_record;
        require!(!fee_record.is_finalized, FeeDistributionError::RentAlreadyFinalized);
        require!(!fee_record.is_reverted,  FeeDistributionError::RentAlreadyReverted);

        let refund_amount = fee_record.governance_fee
            .checked_add(fee_record.owner_fee)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        // Sign with contract PDA
        let contract = &ctx.accounts.contract;
        let seeds = &[b"contract".as_ref(), &[contract.bump]];
        let signer_seeds = &[&seeds[..]];

        let cpi_accounts = Transfer {
            from: ctx.accounts.fee_vault.to_account_info(),
            to: ctx.accounts.payer_token_account.to_account_info(),
            authority: ctx.accounts.contract.to_account_info(),
        };
        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                cpi_accounts,
                signer_seeds,
            ),
            refund_amount,
        )?;

        fee_record.is_reverted = true;

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

    /// Owner-only: update the supply snapshot used for fee calculations.
    /// Should be called periodically via governance to reflect real supply.
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

    pub fn rent_space(
        ctx: Context<RentSpace>,
        recipient: Pubkey,
        expiration_time: i64,
        payment_amount: u64,
    ) -> Result<()> {
        require!(payment_amount > 0, FeeDistributionError::InvalidPaymentAmount);

        let now = Clock::get()?.unix_timestamp;
        require!(expiration_time > now, FeeDistributionError::PaymentExpired);

        require_eq!(
            ctx.accounts.recipient_token_account.owner,
            recipient,
            FeeDistributionError::RecipientMismatch
        );

        // ── Fee split ──────────────────────────────────────────────────────
        // 80% → renter immediately
        // 15% → fee_vault (governance holders, unlocked by finalize_rent)
        //  5% → fee_vault (contract owner,    unlocked by finalize_rent)
        // The 15%+5% stay locked in the vault until expiration.
        // revert_rent can return them to the payer before expiration.
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

        let total_fee = governance_fee
            .checked_add(owner_fee)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        let payment_to_recipient = payment_amount
            .checked_mul(RECIPIENT_PCT)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?
            .checked_div(100)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        // Transfer 80% to recipient immediately
        let cpi_accounts = Transfer {
            from: ctx.accounts.payer_token_account.to_account_info(),
            to: ctx.accounts.recipient_token_account.to_account_info(),
            authority: ctx.accounts.payer.to_account_info(),
        };
        token::transfer(
            CpiContext::new(ctx.accounts.token_program.to_account_info(), cpi_accounts),
            payment_to_recipient,
        )?;

        // Transfer 20% (15% governance + 5% owner) to fee vault — locked until expiry
        let fee_cpi_accounts = Transfer {
            from: ctx.accounts.payer_token_account.to_account_info(),
            to: ctx.accounts.fee_vault.to_account_info(),
            authority: ctx.accounts.payer.to_account_info(),
        };
        token::transfer(
            CpiContext::new(ctx.accounts.token_program.to_account_info(), fee_cpi_accounts),
            total_fee,
        )?;

        // NOTE: accumulator NOT updated here — updated only in finalize_rent
        // (after expiration), so governance holders cannot claim fees that may
        // still be reverted.
        let contract = &mut ctx.accounts.contract;

        let fee_record = &mut ctx.accounts.fee_record;
        fee_record.contract = contract.key();
        fee_record.payer = ctx.accounts.payer.key();
        fee_record.recipient = recipient;
        fee_record.expiration_time = expiration_time;
        fee_record.total_amount = payment_amount;
        fee_record.governance_fee = governance_fee;
        fee_record.owner_fee = owner_fee;
        fee_record.timestamp = now;
        fee_record.index = contract.fee_record_count;
        fee_record.is_finalized = false;
        fee_record.is_reverted = false;
        fee_record.owner_fee_claimed = false;

        contract.fee_record_count = contract
            .fee_record_count
            .checked_add(1)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        emit!(RentSpaceEvent {
            payer: ctx.accounts.payer.key(),
            recipient,
            expiration_time,
            payment_amount,
            governance_fee,
            owner_fee,
            timestamp: now,
        });

        Ok(())
    }

    /// Finalise a rental after its expiration time has passed.
    /// Releases the 15% governance fee into the accumulator (enabling holder
    /// claims) and marks the record as finalized so the owner can withdraw
    /// their 5% via claim_owner_fee.
    /// Anyone can call this — it is a permissionless settlement.
    pub fn finalize_rent(ctx: Context<FinalizeRent>) -> Result<()> {
        let now = Clock::get()?.unix_timestamp;
        let fee_record = &mut ctx.accounts.fee_record;

        require!(
            now >= fee_record.expiration_time,
            FeeDistributionError::RentNotExpired
        );
        require!(!fee_record.is_finalized, FeeDistributionError::RentAlreadyFinalized);
        require!(!fee_record.is_reverted,  FeeDistributionError::RentAlreadyReverted);

        // Release governance fees into the accumulator using the snapshotted supply
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

        fee_record.is_finalized = true;

        emit!(FinalizeRentEvent {
            payer: fee_record.payer,
            index: fee_record.index,
            governance_fee,
            owner_fee: fee_record.owner_fee,
            timestamp: now,
        });

        Ok(())
    }

    /// Revert a rental before it has been finalized.
    /// Returns the full fee (governance 15% + owner 5%) to the original payer.
    /// May be called by the payer themselves or by the contract owner.
    pub fn revert_rent(ctx: Context<RevertRent>) -> Result<()> {
        let fee_record = &mut ctx.accounts.fee_record;

        require!(!fee_record.is_finalized, FeeDistributionError::RentAlreadyFinalized);
        require!(!fee_record.is_reverted,  FeeDistributionError::RentAlreadyReverted);

        // Only the payer (loaner) or the contract owner may revert
        let authority_key = ctx.accounts.authority.key();
        require!(
            authority_key == fee_record.payer || authority_key == ctx.accounts.contract.owner,
            FeeDistributionError::UnauthorizedOwner
        );

        let refund_amount = fee_record.governance_fee
            .checked_add(fee_record.owner_fee)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        // Sign with contract PDA
        let contract = &ctx.accounts.contract;
        let seeds = &[b"contract".as_ref(), &[contract.bump]];
        let signer_seeds = &[&seeds[..]];

        let cpi_accounts = Transfer {
            from: ctx.accounts.fee_vault.to_account_info(),
            to: ctx.accounts.payer_token_account.to_account_info(),
            authority: ctx.accounts.contract.to_account_info(),
        };
        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                cpi_accounts,
                signer_seeds,
            ),
            refund_amount,
        )?;

        fee_record.is_reverted = true;

        emit!(RevertRentEvent {
            payer: fee_record.payer,
            index: fee_record.index,
            refund_amount,
            timestamp: Clock::get()?.unix_timestamp,
        });

        Ok(())
    }

    /// Contract owner withdraws their 5% fee from a finalized rental record.
    pub fn claim_owner_fee(ctx: Context<ClaimOwnerFee>) -> Result<()> {
        let fee_record = &mut ctx.accounts.fee_record;

        require!(fee_record.is_finalized,        FeeDistributionError::RentNotExpired);
        require!(!fee_record.is_reverted,         FeeDistributionError::RentAlreadyReverted);
        require!(!fee_record.owner_fee_claimed,   FeeDistributionError::OwnerFeeAlreadyClaimed);

        require_eq!(
            ctx.accounts.owner.key(),
            ctx.accounts.contract.owner,
            FeeDistributionError::UnauthorizedOwner
        );

        let owner_fee = fee_record.owner_fee;
        require!(owner_fee > 0, FeeDistributionError::NothingToClaim);

        // Sign with contract PDA
        let contract = &ctx.accounts.contract;
        let seeds = &[b"contract".as_ref(), &[contract.bump]];
        let signer_seeds = &[&seeds[..]];

        let cpi_accounts = Transfer {
            from: ctx.accounts.fee_vault.to_account_info(),
            to: ctx.accounts.owner_token_account.to_account_info(),
            authority: ctx.accounts.contract.to_account_info(),
        };
        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                cpi_accounts,
                signer_seeds,
            ),
            owner_fee,
        )?;

        fee_record.owner_fee_claimed = true;

        emit!(ClaimOwnerFeeEvent {
            owner: ctx.accounts.owner.key(),
            payer: fee_record.payer,
            index: fee_record.index,
            amount: owner_fee,
            timestamp: Clock::get()?.unix_timestamp,
        });

        Ok(())
    }

    /// Must be called once per holder before they can claim.
    /// Sets their baseline to the current accumulator so they only
    /// earn fees generated after they register.
    pub fn init_holder_state(ctx: Context<InitHolderState>) -> Result<()> {
        let holder_state = &mut ctx.accounts.holder_state;
        holder_state.holder = ctx.accounts.holder.key();
        // Baseline set to current accumulator — prevents claiming past fees
        holder_state.fees_per_token_claimed = ctx.accounts.contract.fees_per_token_accumulated;
        holder_state.total_claimed = 0;
        Ok(())
    }

    /// Claim fees proportional to current governance token balance.
    /// Uses contract PDA as vault authority to sign the transfer.
    /// 
    /// Anti-double-dip: tokens must be held in the same account registered
    /// at init_holder_state time (enforced via has_one on holder_state).
    /// Moving tokens to a new wallet resets the accumulator baseline.
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

        // Sign with contract PDA seeds (contract is vault authority)
        let seeds = &[b"contract".as_ref(), &[contract.bump]];
        let signer_seeds = &[&seeds[..]];

        let cpi_accounts = Transfer {
            from: ctx.accounts.fee_vault.to_account_info(),
            to: ctx.accounts.holder_token_account.to_account_info(),
            authority: ctx.accounts.contract.to_account_info(),
        };
        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                cpi_accounts,
                signer_seeds,
            ),
            claimable,
        )?;

        // Advance claimed pointer to prevent double-claiming
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

// ================== Oracle Account Structs ==================

#[derive(Accounts)]
pub struct UpdateOracle<'info> {
    #[account(mut, seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,
    /// Must be the current contract owner.
    pub owner: Signer<'info>,
}

/// `reason` is passed as an instruction argument and forwarded to the event;
/// it is NOT stored on-chain (keeps account size fixed).
#[derive(Accounts)]
pub struct RevokeByOracle<'info> {
    #[account(
        seeds = [b"contract"],
        bump = contract.bump
    )]
    pub contract: Account<'info, ContractState>,

    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
        token::mint = contract.governance_token_mint,
    )]
    pub fee_vault: Account<'info, TokenAccount>,

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

    /// Must match `contract.oracle`; checked in instruction logic.
    pub oracle: Signer<'info>,

    /// Token account of the original loaner (payer) — receives the refund.
    #[account(
        mut,
        token::mint = contract.governance_token_mint,
        token::authority = fee_record.payer,
    )]
    pub payer_token_account: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

// ================== Accounts ==================

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

    /// Contract PDA is authority — avoids circular self-authority issue
    #[account(
        init,
        payer = signer,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump,
        token::mint = governance_token_mint,
        token::authority = contract,
    )]
    pub fee_vault: Account<'info, TokenAccount>,

    #[account(mut)]
    pub signer: Signer<'info>,
    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
    pub rent: Sysvar<'info, Rent>,
}

// ── New instruction account structs ──────────────────────────────────────

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
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
        token::mint = contract.governance_token_mint,
    )]
    pub fee_vault: Account<'info, TokenAccount>,

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
pub struct RevertRent<'info> {
    #[account(
        seeds = [b"contract"],
        bump = contract.bump
    )]
    pub contract: Account<'info, ContractState>,

    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
        token::mint = contract.governance_token_mint,
    )]
    pub fee_vault: Account<'info, TokenAccount>,

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

    /// Token account belonging to the original payer (loaner) that will
    /// receive the refunded fees.
    #[account(
        mut,
        token::mint = contract.governance_token_mint,
        token::authority = fee_record.payer,
    )]
    pub payer_token_account: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct ClaimOwnerFee<'info> {
    #[account(
        seeds = [b"contract"],
        bump = contract.bump
    )]
    pub contract: Account<'info, ContractState>,

    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
        token::mint = contract.governance_token_mint,
    )]
    pub fee_vault: Account<'info, TokenAccount>,

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

    pub owner: Signer<'info>,

    #[account(
        mut,
        token::mint = contract.governance_token_mint,
    )]
    pub owner_token_account: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

// ── Original account structs ───────────────────────────────────────────────

#[derive(Accounts)]
#[instruction(recipient: Pubkey, expiration_time: i64, payment_amount: u64)]
pub struct RentSpace<'info> {
    #[account(
        mut,
        seeds = [b"contract"],
        bump = contract.bump
    )]
    pub contract: Account<'info, ContractState>,

    pub governance_token_mint: Account<'info, Mint>,

    #[account(mut)]
    pub payer: Signer<'info>,

    #[account(
        mut,
        token::mint = contract.governance_token_mint,
        token::authority = payer,
    )]
    pub payer_token_account: Account<'info, TokenAccount>,

    #[account(
        mut,
        token::mint = contract.governance_token_mint,
    )]
    pub recipient_token_account: Account<'info, TokenAccount>,

    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
        token::mint = contract.governance_token_mint,
    )]
    pub fee_vault: Account<'info, TokenAccount>,

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

    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct UpdateSupplySnapshot<'info> {
    #[account(
        mut,
        seeds = [b"contract"],
        bump = contract.bump
    )]
    pub contract: Account<'info, ContractState>,
    pub governance_token_mint: Account<'info, Mint>,
    pub owner: Signer<'info>,
}

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
    #[account(
        seeds = [b"contract"],
        bump = contract.bump
    )]
    pub contract: Account<'info, ContractState>,

    #[account(
        mut,
        seeds = [b"fee_vault", contract.key().as_ref()],
        bump = contract.fee_vault_bump,
        token::mint = contract.governance_token_mint,
    )]
    pub fee_vault: Account<'info, TokenAccount>,

    #[account(
        mut,
        seeds = [b"holder_state", holder.key().as_ref()],
        bump,
        has_one = holder,
    )]
    pub holder_state: Account<'info, HolderState>,

    #[account(
        mut,
        token::mint = contract.governance_token_mint,
        token::authority = holder,
    )]
    pub holder_token_account: Account<'info, TokenAccount>,

    pub holder: Signer<'info>,
    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct UpdateOwner<'info> {
    #[account(mut, seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,
    pub owner: Signer<'info>,
}

// ================== State ==================

#[account]
pub struct ContractState {
    pub owner: Pubkey,                      // 32
    pub governance_token_mint: Pubkey,      // 32
    pub oracle: Pubkey,                     // 32 — backend monitoring keypair
    pub bump: u8,                           // 1
    pub fee_vault_bump: u8,                 // 1
    pub fees_per_token_accumulated: u128,   // 16
    pub total_fees_accumulated: u64,        // 8
    pub fee_record_count: u64,              // 8
    pub supply_snapshot: u64,               // 8
}                                           // = 138

#[account]
pub struct FeeRecord {
    pub contract: Pubkey,        // 32
    pub payer: Pubkey,           // 32
    pub recipient: Pubkey,       // 32
    pub expiration_time: i64,    // 8
    pub total_amount: u64,       // 8
    pub governance_fee: u64,     // 8  — 15% locked for governance holders
    pub owner_fee: u64,          // 8  —  5% locked for contract owner
    pub timestamp: i64,          // 8
    pub index: u64,              // 8
    pub is_finalized: bool,      // 1  — true after expiry is acknowledged
    pub is_reverted: bool,       // 1  — true if fees were refunded to payer
    pub owner_fee_claimed: bool, // 1  — true once owner has withdrawn their 5%
}                                // = 147 (+ 5 pad = 152)

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
    pub expiration_time: i64,
    pub payment_amount: u64,
    pub governance_fee: u64,
    pub owner_fee: u64,
    pub timestamp: i64,
}

#[event]
pub struct FinalizeRentEvent {
    pub payer: Pubkey,
    pub index: u64,
    pub governance_fee: u64,
    pub owner_fee: u64,
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
pub struct ClaimOwnerFeeEvent {
    pub owner: Pubkey,
    pub payer: Pubkey,
    pub index: u64,
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
    /// Human-readable reason logged by the backend (e.g. "header_image_changed").
    /// Stored in the transaction log — not in account state.
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
    #[msg("Payment has expired")]
    PaymentExpired,
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
    #[msg("Rental period has not yet expired")]
    RentNotExpired,
    #[msg("This rental record has already been finalized")]
    RentAlreadyFinalized,
    #[msg("This rental record has already been reverted")]
    RentAlreadyReverted,
    #[msg("Owner fee has already been claimed for this record")]
    OwnerFeeAlreadyClaimed,
    #[msg("Signer is not the authorised oracle")]
    UnauthorizedOracle,
}
