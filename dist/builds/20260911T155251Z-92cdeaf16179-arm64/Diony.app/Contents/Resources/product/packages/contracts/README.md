# diony-contracts

The Diony AI machine API. If a field is not on one of these objects, it does not exist for production.

| Module | Holds |
|---|---|
| `director.py` | `DirectorOp` (a priced operation never exists without a reconciling `CostEstimate`), `CloudProvider`, `CloudGenerationMode`, `EngineMode`, `ProductionStyle` |
| `production.py` | `Script`, `Scene`, `ScriptLine`, `CharacterProfile`, `IdentityCard`, `SettingProfile`, `VoiceProfile`, `ClipUnit`, `AudioPacket`, `ContinuityLock`, `PromptVersion`, `ClipReview`, `LookBible`, `MasterPrompt`, `AssemblyMap`, `ProductionPackage` (Blueprints 26–33) |
| `launch.py` | The launch control plane (Blueprint 16): workspaces, memberships, invitations, Cost Centers, workflows, generation jobs, usage and cost allocation, audit |
| `cluster.py` | The engine choice Studio writes and the vault root every surface shares |
| `montage.py` | The program look (`LookProfile`, `look_filter`) and subtitle sidecars (`SubtitleCue`, `render_srt`, `render_vtt`) |
| `testing/` | The offline network guard every test session installs |

```bash
pip install -e packages/contracts
diony-export-schemas          # regenerates jsonschema/ — the sync test refuses a stale export
python -m pytest packages/contracts
```
