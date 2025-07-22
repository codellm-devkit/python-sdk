# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Updated codeanalyzer jar for call argument expression and fully qualified parameter types in method signatures

## [v1.0.5] - 2025-06-24

### Fixed
- Fixed issue #135
- Analysis level compatibility checking for analysis.json with passed analysis level

### Changed
- Updated treesitter analysis to use global declarations of parser and language

## [v1.0.4] - 2025-06-11

### Added
- Added missing callable fields field validator

### Changed
- Updated test fixture setup to use codeanalyzer jar from cldk/analysis/java/codeanalyzer/jar instead of test resources directory
- Updated analysis.json fixtures (daytrader8 and plantsbywebsphere)

### Removed
- Removed dangling codeanalyzer jars from test resources
- Removed obsolete analysis.json fixture

## [v1.0.3] - 2025-06-01

### Added
- Added code start line attribute to JCallable (corresponding to added attribute in the java code analyzer model)

## [v1.0.2] - 2025-05-24

### Added
- Added test case and fixture for source analysis
- Added missing attributes in compilation unit model

### Fixed
- Fixed handling of `source_code` option in Java codeanalyzer
- Updated core.py to match python analysis signature

## [v1.0.1] - 2025-05-07

### Changed
- Updated treesitter analysis to use global declarations of parser and language

## [v1.0.0] - 2025-04-29

### Added
- First stable release
- Updated contributing guidelines

### Changed
- Updated README.md
- Updated codeanalyzer jar
- Updated java version in release automation

## [v0.5.1] - 2025-03-13

### Changed
- Updated Java model to comply with codeanalyzer v2.3.1
- Updated codeanalyzer jar to the latest from codeanalyzer-java
- Updated get_all_docstrings to return dict

## [v0.5.0] - 2025-02-21

### Added
- Added release automation github actions
- Added Java 11 support in github actions
- Added release_config.json
- Added Comment parsing APIs at file, class, method, and docstring level
- Added support for parsing callable parameters and their location information
- Added Dev container instructions with Python, Java, C, and Rust support
- Added C/C++ analysis support
- Added CRUD operations support for Java JPA applications

### Changed
- Consolidated analysis_level enums in __init__.py
- Updated codeanalyzer jar to the latest version
- Changed coverage minimum to 70%
- Updated documentation with mkdocs
- Updated badges and logos in README
- Added Discord community support

### Removed
- Removed CodeQL dependency and refactored treesitter
- Removed ABCs from analysis
- Removed logic to find LLVM in linux OSes (only appears in Darwin)
- Removed redundant is_entry_point fields from JCallable and JType
- Removed unused parameters and code cleanup

### Fixed
- Fixed various test cases and compatibility issues
- Fixed treesitter superclass identification issues
- Fixed entry point detection code
- Fixed recursive error issues

## [v0.4.0] - 2024-11-13

### Fixed
- Fixed issue 67 - symbol table is none

### Changed
- Updated poetry build rules to include codeanalyzer-*.jar
- Added test case to verify jar file exists

## [v0.3.0] - 2024-11-12

### Added
- Support for reading slim JSON from codeanalyzer v1.1.0
- Added more test tools (pylint, flake8, black, pspec, coverage)
- Added test coverage reporting

### Changed
- Updated README.md to include the arXiv paper
- Removed obsolete test cases for unsupported languages

## [v0.2.0] - 2024-10-11

### Added
- Added GitHub Action to publish manual releases
- Added PyPi badge to README.md

## [v0.1.4] - 2024-10-21

### Fixed
- Fixed codeanalyzer.jar not being a PosixPath

## [v0.1.3] - 2024-10-21

### Fixed
- Fixed calling the correct codeanalyzer jar on version 0.1.3
- Removed auto-download of codeanalyzer jar

## [v0.1.2] - 2024-10-17

### Fixed
- Fixed tree-sitter bug
- Defined self.captures explicitly

## [0.1.0-dev] - 2024-10-07

### Added
- Initial development version
- Set version to über json support
- Support for slim JSONs from codeanalyzer
- IBM Copyright added to all source files
- Added code parsing support
- Added support for symbol table call graph
- Added notebook examples for code summarization and test generation
- Basic CLDK framework implementation

### Changed
- Updated dependencies in pyproject.toml
- Added metadata for PyPi distribution
- Updated README with installation instructions

### Fixed
- Fixed caller method implementation
- Fixed incremental analysis support
- Fixed download jar issues

---

## Release Links

- [v1.0.5]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.5
- [v1.0.4]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.4
- [v1.0.3]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.3
- [v1.0.2]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.2
- [v1.0.1]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.1
- [v1.0.0]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.0
- [v0.5.1]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.5.1
- [v0.5.0]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.5.0
- [v0.4.0]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.4.0
- [v0.3.0]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.3.0
- [v0.2.0]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.2.0
- [v0.1.4]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.1.4
- [v0.1.3]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.1.3
- [v0.1.2]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.1.2
- [0.1.0-dev]: https://github.com/codellm-devkit/python-sdk/releases/tag/0.1.0-dev
