"""
Repository Detector
Evidence-based repository stack detection with configurable thresholds.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Optional


# File pattern mappings for language/framework detection
DETECTION_PATTERNS = {
    "javascript": [
        "package.json",
        "*.js",
        "webpack.config.*",
        "vite.config.*",
    ],
    "typescript": [
        "package.json",
        "tsconfig.json",
        "*.ts",
        "tsconfig.*.json",
    ],
    "python": [
        "pyproject.toml",
        "requirements*.txt",
        "setup.py",
        "setup.cfg",
    ],
    "go": ["go.mod", "*.go"],
    "rust": ["Cargo.toml", "*.rs"],
    "java": ["pom.xml", "*.java", "build.gradle", "build.gradle.kts", "settings.gradle"],
    "kotlin": ["build.gradle.kts", "*.kt"],
    "csharp": ["*.csproj", "*.sln"],
    "php": ["composer.json"],
    "ruby": ["Gemfile"],
    "elixir": ["mix.exs"],
    "r": ["*.R"],
    "lua": ["*.lua"],
    "swift": ["Package.swift", "*.swift"],
    "c": ["*.c", "CMakeLists.txt"],
    "cpp": ["*.cpp", "*.cc", "CMakeLists.txt"],
}

FRAMEWORK_PATTERNS = {
    "javascript": ["node_modules", "package-lock.json"],
    "typescript": ["node_modules", "package-lock.json", "yarn.lock"],
    "react": ["node_modules/react", "create-react-app", "vite.config.ts"],
    "vue": ["node_modules/vue", "vue.config.js", "vite.config.ts"],
    "angular": ["node_modules/@angular/core", "angular.json"],
    "fastapi": ["pyproject.toml", "fastapi"],
    "django": ["requirements.txt", "django"],
    "flask": ["requirements.txt", "flask"],
    "express": ["package.json", "express"],
    "golang": ["go.mod"],
    "rust": ["Cargo.toml"],
}

PACKAGE_MANAGER_PATTERNS = {
    "npm": ["package-lock.json", "npm-shrinkwrap.json", "node_modules"],
    "pnpm": ["pnpm-lock.yaml", "node_modules"],
    "yarn": ["yarn.lock"],
    "bun": ["bun.lockb"],
    "pip": ["requirements.txt", "pyproject.toml"],
    "poetry": ["pyproject.toml", "poetry.lock"],
    "uv": ["pyproject.toml", "uv.lock"],
    "go": ["go.mod"],
    "cargo": ["Cargo.toml"],
    "composer": ["composer.lock"],
    "gem": ["Gemfile", "Gemfile.lock"],
    "mix": ["mix.exs"],
}

TEST_FRAMEWORK_PATTERNS = {
    "jest": ["jest.config.js", "jest.config.ts", "jest"],
    "vitest": ["vitest.config.ts", "vitest"],
    "pytest": ["pytest.ini", "pytest", "pyproject.toml"],
    "junit": ["junit", "*.java"],
    "cypress": ["cypress.config.js"],
    "karma": ["karma.conf.js"],
}

CI_PATTERNS = {
    "github_actions": [".github/workflows"],
    "gitlab_ci": [".gitlab-ci.yml"],
    "circleci": ["circleci/config.yml"],
    "buildkite": [".buildkite", "buildkite.json"],
}

IAC_PATTERNS = {
    "terraform": ["*.tf", "Terraform*"],
    "pulumi": ["Pulumi*.yaml", "Pulumi*.json"],
    "cloudformation": ["*.json", "aws/cloudformation"],
    "kubernetes": ["k8s/", "kubernetes/", "helm/"],
}

MCP_PATTERNS = [".mcp.json"]
LSP_PATTERNS = [".lsp.json"]


@dataclass
class DetectionEvidence:
    """Evidence collected during detection."""

    files_checked: int = 0
    language_matches: List[str] = field(default_factory=list)
    framework_matches: List[str] = field(default_factory=list)
    package_manager_matches: List[str] = field(default_factory=list)
    test_framework_matches: List[str] = field(default_factory=list)
    ci_matches: List[str] = field(default_factory=list)
    iac_matches: List[str] = field(default_factory=list)
    mcp_matches: List[str] = field(default_factory=list)
    lsp_matches: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class RepositoryDetector:
    """Detect repository stack and characteristics."""

    def __init__(self, root: str | Path, threshold: float = 0.3):
        """Initialize detector.

        Args:
            root: Repository root path.
            threshold: Minimum threshold for detection confidence.
        """
        self.root = Path(root)
        if not self.root.exists():
            raise FileNotFoundError(f"Repository root not found: {root}")

        self.threshold = threshold
        self.evidence = DetectionEvidence()

    def detect(self) -> Dict:
        """Run detection and return structured result.

        Returns:
            Detection result dictionary matching the schema.
        """
        if not self.root.exists():
            raise FileNotFoundError(f"Root not found: {self.root}")

        # Initialize result
        result = {
            "status": "unclassified",
            "root": str(self.root),
            "languages": [],
            "package_managers": [],
            "frameworks": [],
            "test_commands": [],
            "build_commands": [],
            "lsp_candidates": [],
            "confidence": 0.0,
            "evidence": [],
            "warnings": [],
        }

        # Check for untracked scaffolding (FigmaForge placeholder dirs)
        if self._has_scaffold_files():
            result["warnings"].append("Detected untracked scaffolding files (possibly FigmaForge).")

        # Detect languages and ecosystems
        languages = self._detect_languages()
        result["languages"] = languages

        # Detect frameworks
        frameworks = self._detect_frameworks(languages)
        result["frameworks"] = frameworks

        # Detect package managers
        package_managers = self._detect_package_managers(languages)
        result["package_managers"] = package_managers

        # Detect test frameworks
        test_commands = self._detect_test_commands()
        result["test_commands"] = test_commands

        # Detect CI providers
        ci_providers = self._detect_ci()
        result["ci_providers"] = ci_providers

        # Detect IaC
        iac_tools = self._detect_iac()
        result["iac_tools"] = iac_tools

        # Detect MCP config
        mcp_config = self._find_file(".mcp.json")
        if mcp_config:
            result["mcp_config"] = mcp_config
            result["evidence"].append(".mcp.json found")

        # Detect LSP config
        lsp_config = self._find_file(".lsp.json")
        if lsp_config:
            result["lsp_config"] = lsp_config
            result["evidence"].append(".lsp.json found")

        # Get available language servers from PATH (not auto-activating)
        lsp_candidates = self._get_lsp_candidates(languages)
        result["lsp_candidates"] = lsp_candidates

        # Calculate confidence
        result["confidence"] = self._calculate_confidence(result)

        # Determine status
        if result["confidence"] >= self.threshold:
            result["status"] = "classified"
        else:
            result["status"] = "unclassified"

        return result

    def _has_scaffold_files(self) -> bool:
        """Check for untracked scaffolding files."""
        # .claude placeholder dirs or .gitkeep
        claude_dirs = [".claude/agents", ".claude/skills", ".claude/commands"]
        for d in claude_dirs:
            path = self.root / d
            if path.exists() and (path.is_dir() and not any(p.suffix == ".gitkeep" for p in path.iterdir())):
                return True
        return False

    def _detect_languages(self) -> List[str]:
        """Detect programming languages."""
        languages = []

        # Check each language's file patterns
        for lang, patterns in DETECTION_PATTERNS.items():
            for pattern in patterns:
                # Check if any matching file exists
                if self._file_pattern_matches(pattern):
                    languages.append(lang)
                    break

        return languages

    def _detect_frameworks(self, languages: List[str]) -> List[str]:
        """Detect frameworks based on detected languages."""
        frameworks = []

        for lang in languages:
            if lang not in FRAMEWORK_PATTERNS:
                continue

            for pattern in FRAMEWORK_PATTERNS[lang]:
                if self._file_pattern_matches(pattern):
                    frameworks.append(pattern)
                    break

        return frameworks

    def _detect_package_managers(self, languages: List[str]) -> List[str]:
        """Detect package managers."""
        package_managers = []

        for lang in languages:
            if lang not in PACKAGE_MANAGER_PATTERNS:
                continue

            for pattern in PACKAGE_MANAGER_PATTERNS[lang]:
                if self._file_pattern_matches(pattern):
                    package_managers.append(pattern)
                    break

        return package_managers

    def _detect_test_commands(self) -> List[str]:
        """Detect test framework commands from configs."""
        test_commands = []

        # Check common test config files
        test_configs = ["pytest.ini", "jest.config.js", "vitest.config.ts", "karma.conf.js"]

        for config in test_configs:
            if self._find_file(config):
                test_commands.append("test")
                break

        return test_commands

    def _detect_ci(self) -> List[str]:
        """Detect CI provider presence."""
        ci_providers = []

        for provider, patterns in CI_PATTERNS.items():
            if self._file_pattern_matches(patterns[0]):
                ci_providers.append(provider)
                break

        return ci_providers

    def _detect_iac(self) -> List[str]:
        """Detect Infrastructure as Code tools."""
        iac_tools = []

        for tool, patterns in IAC_PATTERNS.items():
            for pattern in patterns:
                if self._file_pattern_matches(pattern):
                    iac_tools.append(tool)
                    break

        return iac_tools

    def _calculate_confidence(self, result: Dict) -> float:
        """Calculate detection confidence score."""
        base_confidence = 0.0

        # +0.2 per language detected
        base_confidence += len(result["languages"]) * 0.2

        # +0.15 per framework detected
        base_confidence += len(result["frameworks"]) * 0.15

        # +0.1 per package manager detected
        base_confidence += len(result["package_managers"]) * 0.1

        # +0.1 per test framework detected
        base_confidence += len(result["test_commands"]) * 0.1

        # +0.15 per CI detected
        base_confidence += len(result["ci_providers"]) * 0.15

        # +0.1 per IaC tool detected
        base_confidence += len(result["iac_tools"]) * 0.1

        # +0.1 if MCP config found
        if "mcp_config" in result:
            base_confidence += 0.1

        # Cap at 1.0
        return min(base_confidence, 1.0)

    def _get_lsp_candidates(self, languages: List[str]) -> List[str]:
        """Get available language servers from PATH.

        Note: This is for information only — LSP activation is explicit.
        """
        candidates = []

        lsp_mapping = {
            "python": ["pyright", "pyright-langserver"],
            "javascript": ["typescript-language-server"],
            "typescript": ["typescript-language-server"],
            "go": ["gopls"],
            "rust": ["rust-analyzer"],
            "java": ["jdtls"],
            "kotlin": ["kotlin-language-server"],
            "csharp": ["csharp-ls"],
            "swift": ["sourcekit-lsp"],
            "c": ["clangd"],
            "cpp": ["clangd"],
        }

        for lang in languages:
            for lsp in lsp_mapping.get(lang, []):
                if self._is_binary_available(lsp):
                    candidates.append(lsp)

        return candidates

    def _file_pattern_matches(self, pattern: str) -> bool:
        """Check if any file matches the pattern."""
        # Convert wildcard to regex
        regex_pattern = re.compile(
            "^" + pattern.replace(".", r"\.").replace("*", ".*") + "$"
        )

        # Check root directory and subdirectories
        for root_dir, _, files in os.walk(self.root):
            for file in files:
                if regex_pattern.match(file):
                    return True

        return False

    def _find_file(self, filename: str) -> Optional[str]:
        """Find a file by exact name.

        Returns:
            Full path to file or None.
        """
        for root_dir, _, files in os.walk(self.root):
            if filename in files:
                return os.path.join(root_dir, filename)
        return None

    def _is_binary_available(self, binary_name: str) -> bool:
        """Check if a binary exists on PATH."""
        try:
            result = subprocess.run(
                ["which", binary_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
