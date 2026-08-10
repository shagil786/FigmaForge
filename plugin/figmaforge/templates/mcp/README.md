# MCP Templates

This directory contains reusable MCP server templates.

## Usage

To use a template:

1. Review the template file
2. Customize it for your needs
3. Merge it into your repository's `.mcp.json` manually
4. No automatic merging or approval requests

## Templates

### stdio.example.json
Example stdio-based MCP server configuration.

### http-oauth.example.json
Example HTTP-based MCP server with OAuth authentication.

## Security

- All templates use `example.invalid` for URLs
- Use symbolic environment variable names (never actual values)
- No functioning commands in examples
- Templates are strictly inert

## Notes

- `figmaforge mcp render` only writes to stdout — never writes `.mcp.json`
- Never invoke `claude mcp add` or `claude mcp login` based on templates
- Always review and manually approve any template before using
