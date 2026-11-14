# AGENTS.md — AppBoletas

## Git Identity Policy

All commits created by Codex or other AI coding agents in this repository must use the GitHub identity of the repository owner.

**Required identity:**
- GitHub username: `IvesCaceres`
- Git author name: `IvesCaceres`
- Git author email: `109120812+IvesCaceres@users.noreply.github.com`
- Git committer name: `IvesCaceres`
- Git committer email: `109120812+IvesCaceres@users.noreply.github.com`

### Before creating a commit

1. Verify the effective Git author and committer identity.
2. Check for local Git configuration overrides and environment variables that could change the identity.
3. If the identity differs, correct the repository-local configuration before committing.
4. Never create commits using agent-specific identities such as Claude, Codex, or other bot accounts.
5. Never rewrite existing commit history solely to change attribution.
6. Never use `--author` to impersonate another contributor.
7. Do not modify historical commits without explicit authorization.

### Verification commands

```bash
git var GIT_AUTHOR_IDENT
git var GIT_COMMITTER_IDENT
```

Both commands must resolve to the required GitHub identity before committing.

### Scope

This policy applies to all future commits in AppBoletas, including commits generated through automated development workflows.

Do not change the global Git configuration. Any necessary correction must be scoped to this repository.
