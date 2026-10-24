#pragma once

// Copy to TailConfig.h. This prototype does not require an API token.
// Enable only after checking the complete tail mechanism at the intended rate.
constexpr bool TAIL_MCP_API_ENABLED = false;
// Deliberately no movement until safe endpoints are measured on the actual tail.
constexpr int TAIL_TAP_START_ANGLE = 90;
constexpr int TAIL_TAP_END_ANGLE = 90;
// Raise only after checking the mechanism at the higher speed.
constexpr double TAIL_TESTED_MAX_RATE_HZ = 0.3;
