# diony-contracts

Machine API for Diony AI Producer. If a field is not on these objects, it does not exist for production. Human notes attach to Shot and Take IDs. Clip feedback is a `ClipEdit` that mints a child Take; artifacts are immutable per version. New Idea Shots carry a typed `WanPromptPlan`: Subject → Motion → Camera → Aesthetic/Lighting → Environment Dynamics, dual-expert intent, engine/audio strategy, qualified adapters, continuity risks, dispatch diagnostics, negative terms, and a complete prompt receipt (Blueprint 20). A `ProductionStopReceipt` durably records the Keep/Delete choice, completed Take ids, and either the incomplete 720p result receipt or deleted generated-file count.

```bash
pip install -e packages/contracts
diony-export-schemas   # jsonschema/ + examples/pilot.json
python -m pytest packages/contracts
```

See Blueprints `05-producer-pipeline.md` and `20-wan22-prompt-intelligence.md`.
