# diony-contracts

Machine API for Diony AI Producer. If a field is not on these objects, it does not exist for production. Human notes attach to Shot and Take IDs. Clip feedback is a `ClipEdit` that mints a child Take; artifacts are immutable per version.

```bash
pip install -e packages/contracts
diony-export-schemas   # jsonschema/ + examples/pilot.json
python -m pytest packages/contracts
```

See Blueprint `05-producer-pipeline.md`.
