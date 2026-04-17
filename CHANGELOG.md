# Changelog

## [3.1.2] - 2026-04-17
### Added
- Direct file upload support in AI Analysis section for instant summarization.
- Automatic extraction-to-summary pipeline for single-click document analysis.
### Fixed
- Gemini 404 Model Not Found error by ensuring proper `genai.configure` initialization and model mapping.
- Enhanced API error handling for vision-based extraction.

## [3.1.1] - 2026-04-17
### Added
- Horizontal grid layout for Extraction and AI Analysis sections.
- Collapsible "Click to Expand" cards for the extraction workflow.
- Redesigned AI Master Prompt interface using card components.
- Fixed 404 connection error in AI analysis by adding missing backend route.

## [3.1.0] - 2026-04-17

### Added

- **Extract and Summarize Workflow**: Decoupled text extraction and summarization, allowing for manual text entry and immediate AI analysis.
- **Dynamic Quick Analysis Buttons**: Customizable analysis shortcuts in the extraction module, configurable via AI Settings.
- **Collapsible AI Prompts**: Modernized the AI Settings page with collapsible prompt sections and "Drop Default" restoration features.
- **PDF Tool UI Refinement**: Switched to a responsive grid layout to resolve frame overlapping and improve usability.

## [3.0.0] - 2026-04-17

### Added

- **Industrial Extraction Pipeline (Vision LLM)**: Switched to Vision-based document analysis by default for e-Office documents, providing near-perfect OCR and layout understanding.
- **Noting Master Prompt Optimization**: Restored and optimized the core noting generation prompt for higher professional standard output.
- **Download to Desktop**: New feature that automatically saves generated Noting documents to the local Desktop and opens the destination folder instantly.
- **Enhanced API Resilience**: Implemented robust handling for Gemini API 404 and quota errors with automatic fallback and user feedback.
- **Stealth Mode Expansion**: Improved Direct Mode (Stealth) for GeM and TEC centers with better port management and profile isolation.

### Fixed

- **Dashboard Import Errors**: Resolved critical startup crashes related to missing utility functions.
- **Database Modularization**: Migrated to a cleaner, modular database architecture for better performance and scalability.
- **UI Responsiveness**: Optimized the web dashboard with better state management and real-time build logging.

## [2.1.0] - 2026-03-18

### Added

- **Existing Profile Support**: Direct Mode (Stealth) now prioritizes attaching to your already-running regular Chrome on port 9222. This allows you to use your existing GeM login without launching separate profiles.
- **Direct Mode for TEC**: Extended Stealth Mode to the Technical Evaluation Center (TEC). You can now automate technical evaluations directly in your regular, logged-in browser.
- **Active Tab Detection**: If no URL is provided, the bot now automatically picks your current active tab to start processing, enabling a more seamless 'Open & Run' experience.
- **Universal Automation Helpers**: Centralized all browser interaction logic into `utils.py` for uniform behavior across Selenium and Direct modes throughout the app.

### Fixed

- **Bid Downloader Logic**: Refined tab switching and element finding to be more robust in Direct Mode.
- **UI Advisory**: Added clear instructions on how to launch regular Chrome with the required debugging flag.

## [2.0.0] - 2026-03-18

### Features Added (v2.0)

- **Direct Mode (Stealth Mode)**: Implemented `DrissionPage` as an alternative to Selenium to bypass GeM security blocks on debug mode.
- **Cross-Driver Abstraction**: Built a helper layer (`list_visible_elements`, `get_url`, etc.) to run the same automation logic across both Selenium and DrissionPage APIs.
- **ChromeAutomatorDirect**: New dedicated Chrome profile for stable Direct Mode automation.

### Fixes & Improvements (v2.0)

- **URL Normalization**: Enhanced `utils.py` with robust normalization for `file:///` protocols and case-insensitive drive letter comparisons (`d:/` vs `D:/`). This fixed the "tab not found" error for local test pages.
- **API Compatibility**: Resolved critical `AttributeError` and `IndentationError` bugs where Selenium methods were being called on DrissionPage objects.
- **Port Management**: Standardized browser communication using port 9222/9444 to prevent connection errors (`WinError 10061`).
- **Back Button Issue**: Fixed the automatic back-navigation logic that was causing unwanted logouts during bid processing.

### Other Changes (v2.0)

- Improved dashboard reporting and UI consistency for long-running download tasks.
- Standardized internal `VERSION` variables for consistent update detection.
