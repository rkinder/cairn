-- Migration 006: Rename vault_path to kb_path in promotion_candidates table
-- This supports the Phase 4.6 transition from Obsidian Vault to Quartz Knowledge Base

ALTER TABLE promotion_candidates RENAME COLUMN vault_path TO kb_path;
