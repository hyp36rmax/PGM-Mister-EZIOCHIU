# Community Feedback

This document aggregates community observations, testing results, and technical feedback related to the PGM MiSTer FPGA Core.

## Submission Guidelines

When providing feedback, please include:

- MiSTer firmware version
- SDRAM configuration (Single RAM / Dual RAM)
- Video output method (HDMI / Direct Video / Analog I/O)
- Display type (LCD, OLED, CRT, Arcade Monitor, etc.)
- Game title
- Reproduction steps
- Photos, videos, or logs when available

---

# Verified Findings

## Single SDRAM Support

Status: Verified

Summary:
- Core successfully operates using a single SDRAM module.
- Dual SDRAM is no longer required.

Community Verification:
- Multiple users confirmed successful operation via HDMI.

---

## HDMI Compatibility

Status: Verified

Summary:
- Core operates correctly through HDMI output.
- No major HDMI-specific issues currently reported.

---

# Under Investigation

## Single SDRAM + CRT Compatibility

Status: Under Investigation

Observed Behavior:
- HDMI output functions correctly.
- Some users report lack of CRT video output when using Single SDRAM configurations.

Affected Configurations:
- Single SDRAM
- Direct Video
- Analog I/O Board

Not Affected:
- HDMI output

Community Notes:
- Additional testing required.
- Previous Dual SDRAM releases reportedly functioned correctly on CRT displays.

Reference:
- GitHub Issue #1
