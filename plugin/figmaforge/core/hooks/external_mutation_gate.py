#!/usr/bin/env python3
"""
PreToolUse External Mutation Gate
Inspects Bash commands and MCP tool names for creation/update/deletion/publication/deployment/transition/outbound communication.
"""

import sys
import json
import re
from pathlib import Path

# Bash command patterns that represent external mutations
BASH_MUTATION_PATTERNS = [
    r"git push",
    r"git commit",
    r"git push\.", r"git push origin",
    r"npm publish",
    r"yarn publish",
    r"pnpm publish",
    r"cargo publish",
    r"pip publish",
    r"go run (?:main\.py|app\.js|server\.go)",
    r"terraform apply",
    r"terraform apply\.",
    r"terraform destroy",
    r"terraform destroy\.",
    r"kubectl apply",
    r"kubectl apply\.",
    r"kubectl delete",
    r"kubectl delete\.",
    r"ssh ",
    r"curl -X POST",
    r"curl -X PUT",
    r"curl -X PATCH",
    r"curl -X DELETE",
    r"requests\.post",
    r"requests\.put",
    r"requests\.patch",
    r"requests\.delete",
    r"fetch\(\s*['\"]POST['\"]",
    r"fetch\(\s*['\"]PUT['\"]",
    r"fetch\(\s*['\"]PATCH['\"]",
    r"fetch\(\s*['\"]DELETE['\"]",
    r"Jira\.",
    r"Confluence\.",
    r"amend commit",
    r"create issue",
    r"create page",
    r"send email",
    r"send message",
    r"send notification",
    r"transact database",
    r"execute transaction",
    r"update password",
    r"rotate credentials",
    r"install plugin",
    r"register marketplace",
    r"pinchtab mcp add",
    r"claude mcp add",
    r"claude mcp login",
    r"claude settings update",
    r"npm install -g",
    r"brew install",
    r"apt-get install",
    r"pip install -u",
    r"pip install --upgrade",
    r"sudo ",
    r"chmod 777",
    r"chown root",
]

# MCP tool patterns that represent external mutations
MCP_MUTATION_TOOLS = [
    "jira.createIssue",
    "jira.transitionIssue",
    "jira.editIssue",
    "jira.addComment",
    "jira.createIssueLink",
    "confluence.createPage",
    "confluence.updatePage",
    "confluence.createPageDescendant",
    "confluence.createInlineComment",
    "confluence.createFooterComment",
]


def main():
    """Inspect tool call for mutations and return gate result."""
    # Read tool call from stdin (tool invocation details)
    try:
        tool_input = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        # Invalid JSON — assume safe
        sys.exit(0)

    # Check Bash commands
    if "bash" in tool_input or "command" in tool_input:
        bash_cmd = tool_input.get("command", tool_input.get("bash", "")).lower()

        for pattern in BASH_MUTATION_PATTERNS:
            if re.search(pattern, bash_cmd):
                # External mutation detected
                result = {
                    "permissionDecision": "ask",
                    "gate": "external_mutation",
                    "reason": f"Potential external mutation detected: {bash_cmd[:100]}",
                }
                print(json.dumps(result))
                sys.exit(1)

    # Check MCP tool names
    if "tool" in tool_input:
        tool_name = tool_input["tool"].lower()

        for mutation_tool in MCP_MUTATION_TOOLS:
            if mutation_tool.lower() == tool_name:
                # External mutation detected
                result = {
                    "permissionDecision": "ask",
                    "gate": "external_mutation",
                    "reason": f"Potential external mutation via MCP tool: {tool_name}",
                }
                print(json.dumps(result))
                sys.exit(1)

    # No mutation detected — safe
    sys.exit(0)


if __name__ == "__main__":
    main()
