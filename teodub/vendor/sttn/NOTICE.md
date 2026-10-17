STTN architecture copied from YaoFANGUK/video-subtitle-remover, commit
e109b9ddc1d0e8f153199dfa05c1d767546906d8:
- backend/inpaint/sttn/network_sttn.py
- backend/inpaint/utils/spectral_norm.py

Original research: https://github.com/researchmm/STTN (MIT; LICENSE-STTN).
Wrapper repository: https://github.com/YaoFANGUK/video-subtitle-remover
(Apache-2.0; LICENSE-VSR).

Local modifications: removed unused torchvision.models import; spectral_norm
import now relative. Inference adapter, ROI composition, persistence and UI live
in teodub/inpaint_worker.py and subtitle_clean*.py, not in the upstream network.

Checkpoint backend/models/sttn-det/sttn.pth comes from the same pinned commit.
SHA256: 25b0c2c30042d82efd1893bd42ec726764262d94115393a1718f8d65d2a7817b.
Loaded with torch.load(weights_only=True). No ProPainter code or model used.
STTN does not classify language or automatically find subtitle rectangles.
