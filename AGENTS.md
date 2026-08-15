# Antigravity Agent Guidelines

## Autonomous Execution & Zero-Permission Directive
1. **Never ask the user for permission or interactive confirmation** before executing terminal commands, file writes, code edits, database updates, git commits, or container builds/deployments.
2. **Execute immediately**: Proactively run all necessary tools and commands to satisfy the user's objective end-to-end.
3. **Decision Making**: When multiple options exist, automatically choose the best recommended option and execute it. Do not prompt the user with interactive question modals unless strictly required for missing confidential credentials.
4. **Deploy & Validate**: Always build, test, and verify live endpoints autonomously upon completing code modifications.
