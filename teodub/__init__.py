"""Tèo Dub Studio: background dubbing without controlling a video editor."""
__version__ = '0.1.0'

from teodub import vc_runtime as _vc_runtime  # noqa: E402  (old bundles lack Visual C++ files; see that module)
_vc_runtime.ensure()
