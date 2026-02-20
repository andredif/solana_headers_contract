use anchor_lang::prelude::*;
use anchor_spl::token::{self, Transfer, Token};
use anchor_spl::token::TokenAccount;
use anchor_spl::token::Mint;

declare_id!("CkM4AcKu7LSXikXaTvxwuTDBXmT2yrEZTvXbJ6Kdrox7");

const PRECISION: u128 = 1_000_000_000_000;

// Hardcoded space constants (8 = Anchor discriminator)
// ContractState: 32 + 32 + 1 + 1 + 16 + 8 + 8 + 8 = 106
const CONTRACT_STATE_SPACE: usize = 8 + 106;
// FeeRecord: 32 + 32 + 32 + 8 + 8 + 8 + 8 + 8 = 136
const FEE_RECORD_SPACE: usize = 8 + 136;
// HolderState: 32 + 16 + 8 = 56
const HOLDER_STATE_SPACE: usize = 8 + 56;

const PRECISION: u128 = 1_000_000_000_000;

// Hardcoded space constants (8 = Anchor discriminator)
// ContractState: 32 + 32 + 1 + 1 + 16 + 8 + 8 + 8 = 106
const CONTRACT_STATE_SPACE: usize = 8 + 106;
// FeeRecord: 32 + 32 + 32 + 8 + 8 + 8 + 8 + 8 = 136
const FEE_RECORD_SPACE: usize = 8 + 136;
// HolderState: 32 + 16 + 8 = 56
const HOLDER_STATE_SPACE: usize = 8 + 56;

#[program]
pub mod fee_distribution {
    use super::*;

    pub fn initialize(
        ctx: Context<Initialize>,
        owner: Pubkey,
        supply_snapshot: u64,
    ) -> Result<()> {
        require!(supply_snapshot > 0, FeeDistributionError::InvalidSupplySnapshot);

        let contract = &mut ctx.accounts.contract;
        contract.owner = owner;
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

        let contract = &mut ctx.accounts.contract;

        let fee_amount = payment_amount
            .checked_mul(20)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?
            .checked_div(100)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        let payment_to_recipient = payment_amount
            .checked_sub(fee_amount)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        // Transfer 80% to recipient
        let cpi_accounts = Transfer {
            from: ctx.accounts.payer_token_account.to_account_info(),
            to: ctx.accounts.recipient_token_account.to_account_info(),
            authority: ctx.accounts.payer.to_account_info(),
        };
        token::transfer(
            CpiContext::new(ctx.accounts.token_program.to_account_info(), cpi_accounts),
            payment_to_recipient,
        )?;

        // Transfer 20% to fee vault
        let fee_cpi_accounts = Transfer {
            from: ctx.accounts.payer_token_account.to_account_info(),
            to: ctx.accounts.fee_vault.to_account_info(),
            authority: ctx.accounts.payer.to_account_info(),
        };
        token::transfer(
            CpiContext::new(ctx.accounts.token_program.to_account_info(), fee_cpi_accounts),
            fee_amount,
        )?;

        // Update accumulator using snapshotted supply (not live supply)
        let supply = contract.supply_snapshot;
        let fees_per_token_delta = (fee_amount as u128)
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
            .checked_add(fee_amount)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        // Unique fee record per payer per transaction
        let fee_record = &mut ctx.accounts.fee_record;
        fee_record.contract = contract.key();
        fee_record.payer = ctx.accounts.payer.key();
        fee_record.recipient = recipient;
        fee_record.expiration_time = expiration_time;
        fee_record.total_amount = payment_amount;
        fee_record.fee_amount = fee_amount;
        fee_record.timestamp = now;
        fee_record.index = contract.fee_record_count;

        contract.fee_record_count = contract
            .fee_record_count
            .checked_add(1)
            .ok_or(FeeDistributionError::ArithmeticOverflow)?;

        emit!(RentSpaceEvent {
            payer: ctx.accounts.payer.key(),
            recipient,
            expiration_time,
            payment_amount,
            fee_amount,
            timestamp: now,
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
    pub bump: u8,                           // 1
    pub fee_vault_bump: u8,                 // 1
    pub fees_per_token_accumulated: u128,   // 16
    pub total_fees_accumulated: u64,        // 8
    pub fee_record_count: u64,              // 8
    pub supply_snapshot: u64,               // 8
}                                           // = 106

#[account]
pub struct FeeRecord {
    pub contract: Pubkey,       // 32
    pub payer: Pubkey,          // 32
    pub recipient: Pubkey,      // 32
    pub expiration_time: i64,   // 8
    pub total_amount: u64,      // 8
    pub fee_amount: u64,        // 8
    pub timestamp: i64,         // 8
    pub index: u64,             // 8
}                               // = 136

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
    pub fee_amount: u64,
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
}
