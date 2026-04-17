# APMD eOffice Bot — Procurement Assistant

A powerful, AI-driven assistant designed to streamline procurement workflows, e-Office noting, and document processing for Government organizations.

![Smart Bot Dashboard](static/logo.png) <!-- Add your logo or screenshot here -->

## 🚀 Features

- **📝 e-Office Noting**: AI-powered drafting of ministerial and technical notings in both Hindi and English. Includes a searchable standard library.
- **📁 PDF Tools**: High-performance PDF merging and compression (optimized for e-Office 20MB limits).
- **📥 GeM Bid Downloader**: Automate the download of seller documents directly from the GeM portal.
- **✅ TEC Evaluation**: Semi-automated Technical Evaluation Center to analyze and compare bidder qualifications.
- **📖 Know How (Q&A)**: RAG-based AI assistant that answers questions based on your departmental manuals and circulars.
- **🧠 Knowledge Base**: Teach the bot by feeding it manuals, past notings, and guidelines.

## 🛠️ Installation

### Prerequisites

- **Python 3.12+**
- **Google Chrome** (for automation features)
- **Git**

### Step-by-Step Guide

1. **Clone the Repository**

   ```bash
   git clone https://github.com/vivekjui/bot.git
   cd bot
   ```

2. **Create a Virtual Environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install Dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Configure the Bot**
   - Copy `config.json.example` to `config.json`.
   - Open `config.json` and add your **Gemini API Key**.
   - Configure your email and paths if necessary.

5. **Launch the Dashboard**

   ```bash
   python dashboard.py
   ```

   The dashboard will be available at `http://127.0.0.1:5006`.

## 📖 Usage

- **Noting**: Select a procurement stage, pick a template, and use "Refine AI" to customize it for your specific case.
- **PDF Tools**: Access via the sidebar to merge files or compress them below 20MB for e-Office uploads.
- **Update**: Use the **Update Bot** button on the dashboard to pull the latest changes from the GitHub repository.

## 🔒 Security & Privacy

- **Offline Processing**: The bot processes documents locally; only the text sent for AI refinement goes to Google's Gemini API.
- **Credentials**: Never commit your `config.json` or `cases.db` to version control.

## 🤝 Contributing

Contributions are welcome! Please fork the repository and submit a pull request for any enhancements or bug fixes.

---
**Developed by Vivek Jui**
