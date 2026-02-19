use anchor_lang::prelude::*;
use anchor_spl::token::{self, Transfer};
use anchor_spl::token::TokenAccount;

declare_id!("7Wae1NEJ9RJY7oLJaSp5LopFC5Kr2T1e6P16hD3MQfBw");

#[program]
pub mod fee_distribution {
    use super::*;

    /// Initialize the fee distribution contract
    pub fn initialize(
        ctx: Context<Initialize>,
        owner: Pubkey,
        governance_token_mint: Pubkey,
    ) -> Result<()> {
        let contract = &mut ctx.accounts.contract;
        contract.owner = owner;
        contract.governance_token_mint = governance_token_mint;
        contract.bump = ctx.bumps.contract;
        
        Ok(())
    }

    /// Process a rent space payment
    /// This function receives a payment and distributes 20% to token holders
    pub fn rent_space(
        ctx: Context<RentSpace>,
        recipient: Pubkey,
        expiration_time: i64,
        payment_amount: u64,
    ) -> Result<()> {
        require!(payment_amount > 0, FeeDistributionError::InvalidPaymentAmount);
        
        let contract = &ctx.accounts.contract;
        
        // Calculate 20% fee for token holders
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
        
        let cpi_ctx = CpiContext::new(ctx.accounts.token_program.to_account_info(), cpi_accounts);
        token::transfer(cpi_ctx, payment_to_recipient)?;

        // Store fee information for distribution
        let fee_record = &mut ctx.accounts.fee_record;
        fee_record.contract = contract.key();
        fee_record.payer = ctx.accounts.payer.key();
        fee_record.recipient = recipient;
        fee_record.expiration_time = expiration_time;
        fee_record.total_amount = payment_amount;
        fee_record.fee_amount = fee_amount;
        fee_record.timestamp = Clock::get()?.unix_timestamp;

        emit!(RentSpaceEvent {
            payer: ctx.accounts.payer.key(),
            recipient,
            expiration_time,
            payment_amount,
            fee_amount,
            timestamp: Clock::get()?.unix_timestamp,
        });

        Ok(())
    }

    /// Distribute accumulated fees to governance token holders
    pub fn distribute_fees(
        ctx: Context<DistributeFees>,
        token_holder_amount: u64,
    ) -> Result<()> {
        require!(token_holder_amount > 0, FeeDistributionError::InvalidPaymentAmount);

        let contract = &ctx.accounts.contract;

        // Transfer fee amount to fee vault (held for distribution)
        let cpi_accounts = Transfer {
            from: ctx.accounts.payer_token_account.to_account_info(),
            to: ctx.accounts.fee_vault.to_account_info(),
            authority: ctx.accounts.payer.to_account_info(),
        };

        let cpi_ctx = CpiContext::new(ctx.accounts.token_program.to_account_info(), cpi_accounts);
        token::transfer(cpi_ctx, token_holder_amount)?;

        emit!(DistributionEvent {
            contract: contract.key(),
            total_distributed: token_holder_amount,
            timestamp: Clock::get()?.unix_timestamp,
        });

        Ok(())
    }

    /// Update contract owner
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

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(
        init,
        payer = owner,
        space = 8 + std::mem::size_of::<ContractState>(),
        seeds = [b"contract"],
        bump
    )]
    pub contract: Account<'info, ContractState>,
    #[account(mut)]
    pub owner: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct RentSpace<'info> {
    #[account(
        seeds = [b"contract"],
        bump = contract.bump
    )]
    pub contract: Account<'info, ContractState>,
    #[account(mut)]
    pub payer: Signer<'info>,
    #[account(mut)]
    pub payer_token_account: Account<'info, TokenAccount>,
    #[account(mut)]
    pub recipient_token_account: Account<'info, TokenAccount>,
    #[account(
        init,
        payer = payer,
        space = 8 + std::mem::size_of::<FeeRecord>(),
        seeds = [b"fee_record", payer.key().as_ref()],
        bump
    )]
    pub fee_record: Account<'info, FeeRecord>,
    pub token_program: Program<'info, token::Token>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct DistributeFees<'info> {
    #[account(
        seeds = [b"contract"],
        bump = contract.bump
    )]
    pub contract: Account<'info, ContractState>,
    #[account(mut)]
    pub payer: Signer<'info>,
    #[account(mut)]
    pub payer_token_account: Account<'info, TokenAccount>,
    #[account(mut)]
    pub fee_vault: Account<'info, TokenAccount>,
    pub token_program: Program<'info, token::Token>,
}

#[derive(Accounts)]
pub struct UpdateOwner<'info> {
    #[account(mut, seeds = [b"contract"], bump = contract.bump)]
    pub contract: Account<'info, ContractState>,
    pub owner: Signer<'info>,
}

#[account]
pub struct ContractState {
    pub owner: Pubkey,
    pub governance_token_mint: Pubkey,
    pub bump: u8,
}

#[account]
pub struct FeeRecord {
    pub contract: Pubkey,
    pub payer: Pubkey,
    pub recipient: Pubkey,
    pub expiration_time: i64,
    pub total_amount: u64,
    pub fee_amount: u64,
    pub timestamp: i64,
}

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
pub struct DistributionEvent {
    pub contract: Pubkey,
    pub total_distributed: u64,
    pub timestamp: i64,
}

#[error_code]
pub enum FeeDistributionError {
    #[msg("Invalid payment amount")]
    InvalidPaymentAmount,
    #[msg("Arithmetic overflow")]
    ArithmeticOverflow,
    #[msg("Unauthorized owner")]
    UnauthorizedOwner,
}
