# V3 Testing Guide

## Quick Start Commands

| What to Test | Command |
|--------------|---------|
| Shadow mode | `python run.py` (default) |
| Full injection | `V3_ROLLOUT_PHASE=full python run.py` |
| Low intensity | `V3_INTENSITY=0.3 V3_ROLLOUT_PHASE=full python run.py` |
| Medium intensity | `V3_INTENSITY=0.5 V3_ROLLOUT_PHASE=full python run.py` |
| High intensity | `V3_INTENSITY=1.0 V3_ROLLOUT_PHASE=full python run.py` |
| View logs | `tail -f logs/app.log \| grep V3` |

---

## What Each Mode Does

### Shadow Mode (Default)
```bash
python run.py
```
- V3 analyzes every message silently
- **Nothing changes in AI response** - user sees normal behavior
- Metrics collected for analysis
- Use for: Validating detection accuracy

### Full Injection
```bash
V3_ROLLOUT_PHASE=full python run.py
```
- V3 signals ARE injected into prompt
- AI **adapts response style** based on detection
- Use for: Testing that V3 actually changes behavior

### Intensity Levels

| Intensity | What's Injected | Effect |
|-----------|-----------------|--------|
| 0.0 | Nothing | Disabled |
| 0.3 | Context only (task type) | Minimal steering |
| 0.5 | + State (pace/mode/certainty) | Light guidance |
| 0.6 | + Behavior adjustments | **Default** - full V3 |
| 0.8+ | + Safety guards | Maximum + anti-terse protection |

---

## Recommended Starting Settings

### Support Bot
```bash
V3_ROLLOUT_PHASE=shadow    # Start here
V3_INTENSITY=0.7           # Higher reassurance
```
Then ramp: shadow → 1pct → 10% → full

### Dev Copilot
```bash
V3_ROLLOUT_PHASE=shadow
V3_INTENSITY=0.5           # Direct, less fluff
```

### Education / Tutoring
```bash
V3_ROLLOUT_PHASE=shadow
V3_INTENSITY=0.8           # Thorough explanations
```

### General Purpose
```bash
V3_ROLLOUT_PHASE=shadow
V3_INTENSITY=0.6           # Balanced default
```

---

## Red Buttons (Emergency Response)

### Confusion Spike
**Signal**: Users saying "no", "not what I meant", "wrong" more often

**Action**:
```bash
V3_ROLLOUT_PERCENT=0
# or
MEMORY_V3_ENABLED=false
```

### Cost Spike
**Signal**: Token usage per conversation increases

**Action**:
```bash
V3_INTENSITY=0.5  # Reduce adjustments
# or
V3_INTENSITY=0.3  # Context only
```

### Anti-Gaming Spike
**Signal**: Anti-gaming triggers increasing

**Action**:
```bash
# Increase cooldown (edit orchestrator.py)
FRUSTRATED_COOLDOWN_SECONDS=120  # Was 60

# Or disable injection temporarily
V3_ROLLOUT_PERCENT=0
```

---

## Test Scripts

### 1. Signal Detection Test
```bash
source venv/bin/activate
python -c "
from app.memory_v3 import generate_v3_signal

tests = [
    'Fix this bug. Just tell me what to change.',
    'Design a caching system. Include trade-offs.',
    'I dont really understand what async means?',
    'Generate a Python function to validate emails.',
]

for msg in tests:
    signal = generate_v3_signal(msg)
    print(f'\\n--- {msg[:40]}... ---')
    print(f'Context: {signal.context.mode.value}')
    print(f'Pace: {signal.state.pace.value}')
    print(f'Certainty: {signal.state.certainty.value}')
"
```

### 2. Intensity Comparison Test
```bash
source venv/bin/activate
python -c "
from app.memory_v3 import generate_v3_signal

msg = 'fix this bug quick'
signal = generate_v3_signal(msg)

print('=== LOW (0.3) - Context Only ===')
print(signal.to_prompt_block(intensity=0.3))

print('\\n=== MEDIUM (0.5) - Context + State ===')
print(signal.to_prompt_block(intensity=0.5))

print('\\n=== DEFAULT (0.6) - Full Adjustments ===')
print(signal.to_prompt_block(intensity=0.6))

print('\\n=== HIGH (1.0) - Maximum ===')
print(signal.to_prompt_block(intensity=1.0))
"
```

### 3. Cohort Assignment Test
```bash
source venv/bin/activate
python -c "
from app.memory_v3.rollout import V3RolloutManager
import os

# Test at 50% rollout
mgr = V3RolloutManager(rollout_percent=50, phase='ramp')

print('Cohort assignments at 50% rollout:')
print('-' * 40)

for i in range(10):
    user_id = f'user_{i}'
    should_inject = mgr.should_inject(user_id)
    cohort = 'treatment' if should_inject else 'control'
    print(f'{user_id}: cohort={cohort}, inject={should_inject}')
"
```

### 4. Current Config Check
```bash
source venv/bin/activate
python -c "
from app.memory_v3 import get_v3_config, should_inject_v3, get_rollout_manager
import os

config = get_v3_config()
print('Current V3 Configuration:')
print('-' * 40)
for k, v in config.items():
    print(f'{k}: {v}')

print()
print('Would inject for test user?')
print(f'  should_inject_v3(\"user_123\"): {should_inject_v3(\"user_123\")}')

mgr = get_rollout_manager()
print(f'\\nRollout phase: {mgr.phase.value}')
print(f'Rollout percent: {mgr.rollout_percent}%')
"
```

### 5. Full Status Check (Copy-Paste Ready)
```bash
source venv/bin/activate
python -c "
from app.memory_v3 import (
    get_v3_config, should_inject_v3, get_rollout_manager,
    generate_v3_signal, V3_INTENSITY
)

# Config
config = get_v3_config()
mgr = get_rollout_manager()

# Test signal
signal = generate_v3_signal('test message')

print('V3 Status Report')
print('=' * 50)
print(f'Enabled:        {config[\"enabled\"]}')
print(f'Phase:          {config[\"rollout_phase\"]}')
print(f'Rollout %:      {config[\"rollout_percent\"]}%')
print(f'Intensity:      {V3_INTENSITY}')
print()
print(f'Test user_123:')
print(f'  cohort:       {\"treatment\" if should_inject_v3(\"user_123\") else \"control\"}')
print(f'  inject:       {should_inject_v3(\"user_123\")}')
print()
print(f'Metrics:')
metrics = mgr.get_metrics()
print(f'  signals:      {metrics[\"rollout\"][\"total_signals\"]}')
print(f'  injected:     {metrics[\"rollout\"][\"injected\"]}')
print(f'  shadow:       {metrics[\"rollout\"][\"shadow_only\"]}')
"
```

---

## Expected Output Examples

### Shadow Mode Log
```
🧠 Memory V3: Shadow mode (cohort=control)
```

### Injection Mode Log
```
🧠 Memory V3: Behavior signals INJECTED (cohort=treatment)
```

### Prompt Block at 0.6 Intensity
```
<!-- V3 Behavior Signal v1 -->
context_mode: DEBUG
state:
  pace: rushed
  mode: executing
  certainty: confident
intensity: 0.6
adjustments_enabled: true
behavior_adjustments:
  - concise
  - direct
<!-- End V3 Signal -->
```

---

## Behavior Differences by Context

| User Says | Without V3 | With V3 (0.6) |
|-----------|------------|---------------|
| "fix this bug quick" | Long explanation + code | Just the fix, minimal talk |
| "explain async in detail" | Medium response | Thorough + examples + check-in |
| "design a system" | Quick answer | Structured with trade-offs |
| "I'm confused about X" | Normal explanation | Simpler + reassuring tone |

---

## Troubleshooting

### V3 not detecting correctly?
1. Check patterns in `state.py` and `context.py`
2. Run signal detection test above
3. Adjust patterns if needed (wait for data first)

### Responses too terse?
1. Lower intensity by 0.1
2. Or set `V3_INTENSITY=0.5`

### Responses too verbose?
1. Raise intensity by 0.1
2. Check if `pace: deep` is being detected incorrectly

### Cohort not as expected?
1. Cohort is deterministic by user_id hash
2. Same user always gets same cohort
3. Change `V3_ROLLOUT_PERCENT` to adjust ratio
