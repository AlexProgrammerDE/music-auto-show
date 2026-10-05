# Contribute to music-auto-show

Contributions can fix behavior, improve documentation, or add focused tests.

## Before you start

Read [the support guide](SUPPORT.md) for questions and issue routing.
Search existing issues and pull requests. Discuss larger API, architecture, or dependency changes before implementation.

Work from `main` and target that branch in your pull request.
Keep each change focused. Avoid unrelated formatting and dependency updates.

## Prepare a checkout

Use Rust and the Bun version in `.bun-version`. Install native audio/USB prerequisites from [README](README.md).

```bash
bun install --frozen-lockfile
bun install --cwd frontend --frozen-lockfile
```

Run the commands below from the repository root unless a command names another directory.
On Windows, use `gradlew.bat` in place of `./gradlew` for Gradle commands.

## Repository layout

- `src/`: Rust audio, analysis, effects, DMX, and API implementation.
- `frontend/`: React SPA.
- `proto/`: shared protobuf contract.
- `fixtures/`: fixture definitions.
- `docs/`: architecture and migration notes.

## Verify your change

```bash
bun run check
cargo fmt --all -- --check
```

Read [AGENTS.md](AGENTS.md). Start with `cargo run -- --simulate` for local work. Real audio/DMX changes also need a hardware check. Keep the real-time path bounded and preserve fallback behavior. Change protobuf definitions first, then run `bun run proto:generate`. Do not edit generated frontend files or preset UI components. Do not commit model checkpoints. A release build uses `bun run build`.

Run the relevant checks before review. State the command and result in the pull request.
If a check cannot run, explain the missing dependency or service. Do not claim it passed.
Keep generated artifacts consistent with their source and review their diff.

## Style and documentation

Follow the existing code conventions and repository formatter. Keep commit hooks enabled.
Add focused tests for changed logic when practical. Avoid tests that only assert source strings.
Update documentation when commands, APIs, configuration, or expected behavior change.
Keep examples small and reproducible. Preserve exact identifiers, commands, and error messages.

## Open a pull request

Explain the problem and resulting behavior. Link related issues without a placeholder issue number.
State simulation and hardware results separately. Identify API, fixture, or configuration compatibility changes.
Include commands and results. State any runtime checks that remain necessary.
Respond to review with a correction or concrete evidence.

Use Conventional Commits: `type(scope): description`, for example `docs(contributing): explain local validation`.
Use a meaningful scope, or omit it. Keep the subject concise and imperative.
Add a body when the reason or compatibility impact is not obvious.

For vulnerabilities, follow [the security reporting instructions](SECURITY.md).
Remove credentials and private data from examples, logs, and screenshots.
