# Release rollback

FigmaForge releases are self-contained archives. Keep the previous archive
available before installing a new version.

## Restore the Claude Code plugin

1. Stop any active FigmaForge run.
2. Remove or rename the currently loaded plugin directory.
3. Extract the previous `figmaforge-<version>.tar.gz` archive.
4. Start Claude Code with the extracted plugin path:

```bash
claude --plugin-dir /path/to/figmaforge-<version>/plugin/figmaforge
```

## Restore the runtime

The archive contains the compiled runtime under `runtime/dist`:

```bash
node /path/to/figmaforge-<version>/runtime/dist/src/cli/main.js --help
```

The release archive also contains `release-manifest.json`, which records a
SHA-256 digest for every packaged file. Verify the archive contents before
using it in CI or production.

## Create a rollback-ready package

From the repository root:

```bash
./scripts/package_release.sh ./release
```

The command runs the release metadata/build checks first and writes a versioned
`.tar.gz` archive without modifying source files.
