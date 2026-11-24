# STUDYVERSE — Study Together. Grow Together.

Production-ready full-stack Class 12 academic preparation, accountability, and real-time community platform for **MP Board (MPBSE)** and **CBSE** students.

## Quick Start

1. Configure Super Administrator credentials in `.env` (see `.env.example`):
   ```bash
   cp .env.example .env
   # Edit ADMIN_1_USERNAME and ADMIN_1_PASSWORD
   ```
2. Install dependencies (if running outside the preconfigured environment):
   ```bash
   pip install -r requirements.txt
   ```
3. Start the server:
   ```bash
   python3 run.py
   ```
4. Open `http://localhost:8000` for the Student Platform and `http://localhost:8000/admin` for the Super Administrator Console.

## Official Timetable Sources (Verified 2026-11-24)
- **CBSE Class 12 (2027)**: [CBSE Official Date Sheet PDF](https://www.cbse.gov.in/cbsenew/documents/Datesheet_ClassX%20_XII_2027_Main_06112026.pdf) (16 Feb – 09 Apr 2027, 10:30 AM IST)
- **MP Board Class 12 (2027)**: [MPBSE Official Portal](https://mpbse.nic.in/) (17 Feb – 19 Mar 2027, 09:00 AM – 12:00 PM IST)
