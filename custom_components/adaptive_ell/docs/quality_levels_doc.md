# Quality Level System

This document defines the User Acceptance Testing (UAT) requirements for promoting modules through quality levels.

## Overview

Each calibration phase module progresses through four quality levels:

```
Alpha → Beta → Silver → Gold
```

**Promotion Requirements:** A module must pass ALL UAT tests for its current level before advancing to the next level.

## Quality Level Definitions

### Alpha: Code Works

**Definition:** Code functions in basic scenarios with known limitations.

**Characteristics:**
- Happy path works
- Known issues documented
- May fail in edge cases
- Error handling minimal
- User experience rough

**Promotion to Beta:** Pass all Beta UAT tests

---

### Beta: Reliable in Optimal Conditions

**Definition:** Code works consistently when all devices are functioning properly.

**Characteristics:**
- Reliable in normal conditions
- Reproducible results
- Basic error handling
- Clear success/failure indicators
- May fail with device issues

**Promotion to Silver:** Pass all Silver UAT tests

---

### Silver: Graceful Error Handling

**Definition:** Code handles errors gracefully with clear reporting and recovery options.

**Characteristics:**
- Handles device failures
- Reports specific errors
- Offers recovery guidance
- Partial success supported
- User gets clear feedback

**Promotion to Gold:** Pass all Gold UAT tests

---

### Gold: Self-Healing & Multiple Solutions

**Definition:** Code anticipates problems, self-heals when possible, and offers multiple solutions when it can't.

**Characteristics:**
- Automatic retry logic
- Progressive timeout adjustment
- Alternative approaches when primary fails
- Predictive failure detection
- Optimal user experience

**Maintenance:** Periodic regression testing

---

## Module-Specific UAT Requirements

### restore_state.py

#### Alpha → Beta UAT

**Requirements:** All tests must pass 5 consecutive times with randomized initial states

**Test Suite:**

1. **Restore Lights from OFF to OFF**
   - Initial: 5 lights OFF
   - After calibration: All lights OFF
   - Validation: All lights report OFF state
   - Success Criteria: 5/5 consecutive passes

2. **Restore Lights from ON to ON (Full Brightness)**
   - Initial: 5 lights ON at 100%
   - After calibration: All lights ON at 100%
   - Validation: All lights ON, brightness 255
   - Success Criteria: 5/5 consecutive passes

3. **Restore Lights with Various Brightness**
   - Initial: 5 lights ON at 20%, 40%, 60%, 80%, 100%
   - After calibration: All lights at correct brightness
   - Validation: Brightness within ±5% of original
   - Success Criteria: 5/5 consecutive passes

4. **Restore RGB Color Lights**
   - Initial: 3 lights with different RGB colors
   - After calibration: All lights at correct color
   - Validation: RGB values within ±10 per channel
   - Success Criteria: 5/5 consecutive passes

5. **Restore Color Temperature Lights**
   - Initial: 3 lights at different color temps (2700K, 4000K, 6500K)
   - After calibration: All lights at correct temp
   - Validation: Color temp within ±200K
   - Success Criteria: 5/5 consecutive passes

#### Beta → Silver UAT

**Requirements:** All Beta tests + all Silver tests pass 10 consecutive times including deliberate failures

**Test Suite (Additional to Beta):**

6. **Handle Unavailable Light During Restore**
   - Initial: 5 lights with known states
   - Test: Mark 2 lights unavailable during restore
   - Expected: Available lights restore, unavailable logged
   - Success Criteria: 10/10 consecutive passes

7. **Handle Light That Fails to Restore**
   - Initial: 5 lights with known states
   - Test: Manually disable 1 light's network during restore
   - Expected: Other lights restore, failure logged with entity_id
   - Success Criteria: 10/10 consecutive passes

8. **Return Structured Error Report**
   - Test: Restore with 2 deliberate failures
   - Expected: Return dict with success/failure per light
   - Format: `{"light.a": "success", "light.b": "failed"}`
   - Success Criteria: 10/10 consecutive passes

9. **Continue After Partial Failure**
   - Test: 10 lights, force 3 to fail
   - Expected: 7 restore successfully, 3 reported failed
   - Validation: No exceptions thrown, all attempts made
   - Success Criteria: 10/10 consecutive passes

#### Silver → Gold UAT

**Requirements:** All Silver tests + all Gold tests pass 20 consecutive times with various failure scenarios

**Test Suite (Additional to Silver):**

10. **Retry Failed Restorations**
    - Test: Light fails first attempt, succeeds on retry
    - Expected: 3 retry attempts with exponential backoff
    - Timing: 1s, 2s, 4s delays between attempts
    - Success Criteria: 20/20 consecutive passes

11. **User Notification of Results**
    - Test: Restore with mixed success/failure
    - Expected: User notification with summary
    - Content: "Restored 7/10 lights. Failed: [list]"
    - Success Criteria: 20/20 consecutive passes

12. **Manual Restore Option**
    - Test: Automatic restore fails for 2 lights
    - Expected: Offer service call to manually restore
    - Content: Service name and entity list provided
    - Success Criteria: 20/20 consecutive passes

13. **Performance Under Load**
    - Test: Restore 50+ lights simultaneously
    - Expected: Complete within 30 seconds
    - Validation: All working lights restored
    - Success Criteria: 20/20 consecutive passes

---

### test_min_max.py

#### Alpha → Beta UAT

**Requirements:** All tests must pass 5 consecutive times

**Test Suite:**

1. **Basic Min/Max Detection**
   - Test: 5 lights in room
   - Expected: max_lux > min_lux by at least 100
   - Validation: Values logical and consistent
   - Success Criteria: 5/5 consecutive passes

2. **Validation Rejects Invalid Data**
   - Test: Simulate min_lux >= max_lux
   - Expected: Raises HomeAssistantError
   - Validation: Clear error message
   - Success Criteria: 5/5 consecutive passes

3. **Consistent Results**
   - Test: Run twice on same room immediately
   - Expected: Results within 10% of each other
   - Validation: Reproducible measurements
   - Success Criteria: 5/5 consecutive passes

4. **Large Light Count**
   - Test: 20+ lights in room
   - Expected: Completes successfully
   - Validation: max_lux significantly > min_lux
   - Success Criteria: 5/5 consecutive passes

5. **Small Light Count**
   - Test: 1-2 lights in room
   - Expected: Completes successfully
   - Validation: Reasonable min/max values
   - Success Criteria: 5/5 consecutive passes

#### Beta → Silver UAT

**Requirements:** All Beta tests + all Silver tests pass 10 consecutive times

**Test Suite (Additional to Beta):**

6. **Handle Lights That Don't Turn On**
   - Test: 1-2 lights fail to turn on for max test
   - Expected: Continue with working lights, log failure
   - Validation: max_lux still > min_lux
   - Success Criteria: 10/10 consecutive passes

7. **Handle Lights That Don't Turn Off**
   - Test: 1-2 lights fail to turn off for min test
   - Expected: Continue with warning, adjust min_lux
   - Validation: min_lux accounts for stuck lights
   - Success Criteria: 10/10 consecutive passes

8. **Sensor Temporarily Unavailable**
   - Test: Sensor unavailable during one reading
   - Expected: Retry with backoff, then fail gracefully
   - Validation: Clear error about sensor issue
   - Success Criteria: 10/10 consecutive passes

#### Silver → Gold UAT

**Requirements:** All Silver tests + all Gold tests pass 20 consecutive times

**Test Suite (Additional to Silver):**

9. **Progressive Timeout Adjustment**
   - Test: Some lights slow to respond
   - Expected: Automatically increase settle time
   - Validation: Accurate readings despite slow lights
   - Success Criteria: 20/20 consecutive passes

10. **Outlier Detection**
    - Test: One reading significantly different
    - Expected: Automatically re-test for confirmation
    - Validation: Outliers identified and handled
    - Success Criteria: 20/20 consecutive passes

---

### test_individual_lights.py

#### Alpha → Beta UAT

**Requirements:** All tests must pass 5 consecutive times with 0% unexplained failures

**Test Suite:**

1. **All Working Lights Detected**
   - Test: 10 working lights
   - Expected: All 10 pass or have clear failure reason
   - Validation: 0 lights fail without explanation
   - Success Criteria: 5/5 consecutive passes

2. **Contribution Threshold Respected**
   - Test: Mix of bright (>100 lux) and dim (<10 lux) lights
   - Expected: Only bright lights included
   - Validation: Dim lights logged as below threshold
   - Success Criteria: 5/5 consecutive passes

3. **Individual Contribution Accuracy**
   - Test: Each light's contribution measured
   - Expected: Re-test shows within 15% variance
   - Validation: Repeatable measurements
   - Success Criteria: 5/5 consecutive passes

4. **Light State Validation**
   - Test: Verify each light actually turns on before reading
   - Expected: Skip lights that fail to turn on
   - Validation: No false readings from OFF lights
   - Success Criteria: 5/5 consecutive passes

5. **Large Scale Testing**
   - Test: 20+ lights
   - Expected: All working lights tested successfully
   - Validation: Complete without timeout errors
   - Success Criteria: 5/5 consecutive passes

#### Beta → Silver UAT

**Requirements:** All Beta tests + all Silver tests pass 10 consecutive times

**Test Suite (Additional to Beta):**

6. **Handle Non-Responsive Light**
   - Test: 1 light doesn't turn on
   - Expected: Skip that light, continue with others
   - Validation: Specific entity_id logged with reason
   - Success Criteria: 10/10 consecutive passes

7. **Handle Slowly Responding Light**
   - Test: 1 light takes 8+ seconds to stabilize
   - Expected: Auto-adjust timeout for that light
   - Validation: Accurate reading despite slow response
   - Success Criteria: 10/10 consecutive passes

8. **Progress Reporting**
   - Test: Testing 15 lights
   - Expected: Progress updates every few lights
   - Format: "Testing light 5/15"
   - Success Criteria: 10/10 consecutive passes

9. **Partial Success Handling**
   - Test: 10 lights, 3 fail to respond
   - Expected: 7 successful measurements returned
   - Validation: Can proceed to next phase
   - Success Criteria: 10/10 consecutive passes

#### Silver → Gold UAT

**Requirements:** All Silver tests + all Gold tests pass 20 consecutive times

**Test Suite (Additional to Silver):**

10. **Intelligent Timeout Adjustment**
    - Test: Mix of fast and slow lights
    - Expected: Short timeout for fast, long for slow
    - Validation: Optimal total time for all lights
    - Success Criteria: 20/20 consecutive passes

11. **Retry Logic for Transient Failures**
    - Test: Light fails once, succeeds on retry
    - Expected: Automatic retry (3 attempts max)
    - Validation: Transient failures don't lose lights
    - Success Criteria: 20/20 consecutive passes

12. **Alternative Testing Methods**
    - Test: Light doesn't respond to white command
    - Expected: Try other color modes (RGB, etc.)
    - Validation: Successfully test lights with limited modes
    - Success Criteria: 20/20 consecutive passes

---

### validate_combinations.py

#### Alpha → Beta UAT

**Requirements:** All tests must pass 5 consecutive times

**Test Suite:**

1. **Pair Additivity Validation**
   - Test: 3 pairs of lights
   - Expected: All pairs within 30% error tolerance
   - Validation: Combined lux ≈ sum of individuals
   - Success Criteria: 5/5 consecutive passes

2. **Detection of Non-Linear Interactions**
   - Test: Pair with >30% error
   - Expected: Marked as invalid, logged clearly
   - Validation: Validation result includes error %
   - Success Criteria: 5/5 consecutive passes

3. **Insufficient Lights Handling**
   - Test: Only 1 contributing light
   - Expected: Skip validation gracefully
   - Validation: Log "not enough lights for pairs"
   - Success Criteria: 5/5 consecutive passes

#### Beta → Silver UAT

**Requirements:** All Beta tests + all Silver tests pass 10 consecutive times

**Test Suite (Additional to Beta):**

4. **Handle Pair Test Failure**
   - Test: One light in pair fails to turn on
   - Expected: Skip that pair, test other pairs
   - Validation: Partial validation results returned
   - Success Criteria: 10/10 consecutive passes

5. **Comprehensive Pair Testing**
   - Test: 5 lights (10 possible pairs)
   - Expected: Test at least 50% of pairs
   - Validation: Results for multiple pairs
   - Success Criteria: 10/10 consecutive passes

#### Silver → Gold UAT

**Requirements:** All Silver tests + all Gold tests pass 20 consecutive times

**Test Suite (Additional to Silver):**

6. **Use Validation Results**
   - Test: Pair with 25% error
   - Expected: Adjust individual light weights to improve
   - Validation: Re-test shows reduced error
   - Success Criteria: 20/20 consecutive passes

7. **Multi-Light Combinations**
   - Test: 3-light combinations
   - Expected: Test additivity of 3+ lights
   - Validation: Better accuracy assessment
   - Success Criteria: 20/20 consecutive passes

---

### save_calibration.py

#### Alpha → Beta UAT

**Requirements:** All tests must pass 5 consecutive times

**Test Suite:**

1. **Successful Data Persistence**
   - Test: Save calibration data
   - Expected: Data in config entry after save
   - Validation: Can reload data after HA restart
   - Success Criteria: 5/5 consecutive passes

2. **Data Integrity**
   - Test: Save then immediately load
   - Expected: All fields match original
   - Validation: No data corruption or loss
   - Success Criteria: 5/5 consecutive passes

3. **Overwrite Previous Calibration**
   - Test: Save calibration twice for same room
   - Expected: Second save replaces first
   - Validation: Only latest data present
   - Success Criteria: 5/5 consecutive passes

#### Beta → Silver UAT

**Requirements:** All Beta tests + all Silver tests pass 10 consecutive times

**Test Suite (Additional to Beta):**

4. **Backup Before Overwrite**
   - Test: Save calibration on already-calibrated room
   - Expected: Previous calibration backed up
   - Validation: Backup recoverable if needed
   - Success Criteria: 10/10 consecutive passes

5. **Partial Save Rollback**
   - Test: Simulate failure during save
   - Expected: Original data intact, no corruption
   - Validation: Can retry save successfully
   - Success Criteria: 10/10 consecutive passes

6. **Save Validation**
   - Test: Attempt to save invalid data
   - Expected: Validation error before write
   - Validation: Config entry not corrupted
   - Success Criteria: 10/10 consecutive passes

#### Silver → Gold UAT

**Requirements:** All Silver tests + all Gold tests pass 20 consecutive times

**Test Suite (Additional to Silver):**

7. **Calibration Versioning**
   - Test: Save multiple calibrations over time
   - Expected: Version history maintained
   - Validation: Can compare calibrations
   - Success Criteria: 20/20 consecutive passes

8. **Automatic Backup Cleanup**
   - Test: 10+ calibrations over time
   - Expected: Old backups auto-deleted (keep 5 most recent)
   - Validation: Storage usage bounded
   - Success Criteria: 20/20 consecutive passes

---

## General Testing Requirements

### All Quality Levels

**Hardware Requirements:**
- Minimum 3 test rooms with different configurations
- Mix of light types (dimmable, RGB, color temp, on/off only)
- At least 1 lux sensor
- Some deliberately misbehaving devices (slow, unreliable)

**Documentation Requirements:**
- Every test run must include:
  - Date and time
  - Hardware configuration
  - HA version
  - Complete logs
  - Screenshots of results
  - Pass/fail for each test

**Regression Testing:**
- When fixing bugs, re-run all UAT tests for current quality level
- Previous level tests must still pass (no regressions)

### Promotion Process

1. **Announce Intent:** Open GitHub Issue announcing promotion attempt
2. **Run Tests:** Complete all UAT tests for target level
3. **Document Results:** Upload test results to GitHub
4. **Submit PR:** Include test evidence in PR description
5. **Peer Review:** Another contributor reviews test evidence
6. **Merge:** Update module header to new quality level

### Failure Handling

**If UAT Fails:**
1. Document which test failed
2. Update KNOWN ISSUES in module header
3. Fix the issue
4. Restart UAT from beginning (no partial credit)

**Consecutive Pass Requirement:**
- Tests must pass consecutively without cherry-picking
- Any failure resets the counter to 0
- Document all failures for learning

---

## Quality Metrics

### Module Quality Dashboard

Track for each module:
```
| Module                        | Level  | UAT Status | Last Test | Blocker |
|-------------------------------|--------|------------|-----------|---------|
| restore_state.py              | Alpha  | BROKEN     | 2025-09-29| OFF bug |
| test_min_max.py               | Alpha  | PASS 3/5   | 2025-09-29| None    |
| test_individual_lights.py     | Alpha  | BROKEN     | 2025-09-29| 63% fail|
| validate_combinations.py      | Alpha  | PASS 5/5   | 2025-09-29| None    |
| save_calibration.py           | Alpha  | PASS 5/5   | 2025-09-29| None    |
```

### Overall Integration Status

**Release Criteria:**
- All modules at least Silver
- Full integration test passes (all modules together)
- Multi-room test passes (3+ rooms)
- Documentation complete

**Post-Release:**
- Maintain Gold level for critical modules (restore_state, test_individual_lights)
- Accept Beta for less critical modules (validate_combinations)

---

## Questions & Support

- **How do I test a module?** See CONTRIBUTING.md for setup
- **Where do I upload test results?** GitHub Issue or PR description
- **Who reviews UAT results?** Any contributor can review (community validation)
- **What if I can't reproduce an issue?** Document this and open discussion

## Updates to This Document

This document is living and will be updated as we learn what works. Changes require:
- Discussion in GitHub Issue
- Agreement from 2+ contributors
- Update PR with justification
