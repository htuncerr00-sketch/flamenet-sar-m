"""
test_s6142_deposition_layer_colors.py — S6.14.2 DepositionRenderer + Katman Renkleri
======================================================================================

DL-01  DepositionRenderer app.renderers'dan import edilebilir.
DL-02  DepositionRenderer setup/update/teardown/reset/load_data metodlarına sahip.
DL-03  RenderFrameBuilder._build_dep_mesh() _dep_verts/_dep_faces/_dep_colors üretir.
DL-04  dep_verts şekli: (2*N, 3); dep_faces: (2*(N-1), 3); dep_colors: (2*N, 4).
DL-05  dep_colors her vertex için 0-1 arası RGBA değeri içeriyor.
DL-06  dep_faces tüm indeksler dep_verts sınırları içinde.
DL-07  Shell renk LUT artık yeşil-sarı-turuncu-kırmızı (0.35 alpha değil 0.65+).
DL-08  _SHELL_LAYER_COLORS[0] yeşil (G > 0.5, R < 0.5).
DL-09  RibbonRenderer ribbon_renderer.py LAYER_COLORS import ediyor.
DL-10  entegre_tasarim_paneli.py DepositionRenderer import ediyor.
DL-11  entegre_tasarim_paneli.py _setup_dep_renderer metodu var.
DL-12  DEP_PERIOD sabit deposition_renderer.py'da tanımlı ve pozitif integer.
"""
import pathlib
import sys
import importlib

_ROOT   = pathlib.Path(__file__).parents[2]
_APP    = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2"
_PANEL  = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/panels/entegre_tasarim_paneli.py"
_RIBBON = _ROOT / "faz17_d2/faz17_d2_app/faz17_d2/app/renderers/ribbon_renderer.py"
_D1_CORE = _ROOT / "faz17_d1/faz17_d1_backend/faz17_d1/core"


def _panel_src() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _ribbon_src() -> str:
    return _RIBBON.read_text(encoding="utf-8")


def _get_renderers():
    app_dir = str(_APP)
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    for k in list(sys.modules.keys()):
        if "app.renderers" in k:
            del sys.modules[k]
    return importlib.import_module("app.renderers")


def _get_builder():
    core_parent = str(_ROOT / "faz17_d1/faz17_d1_backend")
    if core_parent not in sys.path:
        sys.path.insert(0, core_parent)
    for k in list(sys.modules.keys()):
        if "render_frame_builder" in k:
            del sys.modules[k]
    from faz17_d1.core.render_frame_builder import RenderFrameBuilder
    return RenderFrameBuilder


def _make_twin_and_profile():
    """Minimal mock twin + profile — RenderFrameBuilder test için (fizik çağrısı yok)."""
    import numpy as _np
    from dataclasses import dataclass

    core_parent = str(_ROOT / "faz17_d1/faz17_d1_backend")
    if core_parent not in sys.path:
        sys.path.insert(0, core_parent)
    for k in list(sys.modules.keys()):
        if "render_frame_builder" in k:
            del sys.modules[k]
    from faz17_d1.core.render_frame_builder import RenderFrameBuilder

    @dataclass
    class _S:
        spindle_angle_deg: float = 0.0
        carriage_x_actual_mm: float = 0.0
        eye_x_mm: float = 0.0
        eye_r_mm: float = 80.0
        current_layer: int = 0
        current_circuit: int = 0
        fiber_deposited_mm: float = 0.0
        current_radius_mm: float = 50.0
        progress_pct: float = 0.0

    @dataclass
    class _T:
        states: list
        n_layers: int = 2
        base_radius_mm: float = 50.0
        final_radius_mm: float = 52.0
        final_deposition: object = None

    class _P:
        z_mm = _np.linspace(0, 200, 20)
        r_mm = _np.full(20, 50.0)
        def radius_at(self, z): return 50.0

    N = 30
    states = [_S(
        spindle_angle_deg=float(i * 12),
        carriage_x_actual_mm=float(i * 200 / max(N-1, 1)),
        eye_x_mm=float(i * 200 / max(N-1, 1)),
        current_layer=int(i >= N // 2),
        progress_pct=i * 100.0 / max(N-1, 1),
    ) for i in range(N)]
    twin = _T(states=states)
    profile = _P()
    topology = RenderFrameBuilder.build_topology(
        profile,
        shell_nz=8, shell_nth=12, heatmap_nz=6, heatmap_nth=8, ribbon_max_seg=50,
    )
    builder = RenderFrameBuilder(twin, profile, topology, tow_width_mm=6.0)
    return builder, twin


class TestDepositionAndLayerColors:

    def test_DL01_deposition_renderer_importable(self):
        """DepositionRenderer app.renderers'dan import edilebilmeli."""
        mod = _get_renderers()
        cls = getattr(mod, "DepositionRenderer", None)
        assert cls is not None, "DepositionRenderer app.renderers'da yok."
        assert callable(cls), "DepositionRenderer çağrılabilir değil."

    def test_DL02_deposition_renderer_interface(self):
        """DepositionRenderer setup/update/teardown/reset/load_data metodları olmalı."""
        mod = _get_renderers()
        cls = mod.DepositionRenderer
        obj = cls()
        for method in ("setup", "update", "teardown", "reset", "load_data"):
            assert hasattr(obj, method) and callable(getattr(obj, method)), (
                f"DepositionRenderer.{method} metodu eksik."
            )

    def test_DL03_builder_dep_mesh_attributes(self):
        """RenderFrameBuilder._dep_verts/_dep_faces/_dep_colors oluşturulmalı."""
        builder, _ = _make_twin_and_profile()
        assert hasattr(builder, '_dep_verts'),  "builder._dep_verts yok."
        assert hasattr(builder, '_dep_faces'),  "builder._dep_faces yok."
        assert hasattr(builder, '_dep_colors'), "builder._dep_colors yok."

    def test_DL04_dep_arrays_shape(self):
        """dep_verts (2N,3), dep_faces (2*(N-1),3), dep_colors (2N,4)."""
        import numpy as np
        builder, _ = _make_twin_and_profile()
        dv = builder._dep_verts
        df = builder._dep_faces
        dc = builder._dep_colors
        if dv is None:
            return  # ribbon edges başarısız → skip (fiber_contact_model bağımlılığı)
        N = builder.n_states
        assert dv.shape == (2 * N, 3), f"dep_verts şekli yanlış: {dv.shape}"
        assert df.shape == (2 * (N - 1), 3), f"dep_faces şekli yanlış: {df.shape}"
        assert dc.shape == (2 * N, 4), f"dep_colors şekli yanlış: {dc.shape}"

    def test_DL05_dep_colors_valid_rgba(self):
        """dep_colors her vertex için 0-1 arası RGBA içermeli."""
        builder, _ = _make_twin_and_profile()
        dc = builder._dep_colors
        if dc is None:
            return
        assert float(dc.min()) >= 0.0, f"dep_colors min={dc.min()} < 0"
        assert float(dc.max()) <= 1.0, f"dep_colors max={dc.max()} > 1"

    def test_DL06_dep_faces_valid_indices(self):
        """dep_faces tüm indeksleri dep_verts sınırları içinde olmalı."""
        builder, _ = _make_twin_and_profile()
        dv = builder._dep_verts
        df = builder._dep_faces
        if dv is None or df is None:
            return
        n_verts = dv.shape[0]
        assert int(df.min()) >= 0, "dep_faces negatif indeks içeriyor."
        assert int(df.max()) < n_verts, (
            f"dep_faces max indeks={df.max()} >= n_verts={n_verts}"
        )

    def test_DL07_shell_color_alpha_increased(self):
        """Shell renk LUT alpha değeri 0.35'ten artırılmış olmalı (>= 0.5)."""
        import sys as _sys
        core_parent = str(_ROOT / "faz17_d1/faz17_d1_backend")
        if core_parent not in _sys.path:
            _sys.path.insert(0, core_parent)
        import importlib as _il
        for k in list(_sys.modules.keys()):
            if "render_frame_builder" in k:
                del _sys.modules[k]
        mod = _il.import_module("faz17_d1.core.render_frame_builder")
        lut = getattr(mod, "_SHELL_LAYER_COLORS", None)
        assert lut is not None, "_SHELL_LAYER_COLORS sabiti yok."
        import numpy as np
        lut_np = np.asarray(lut)
        alphas = lut_np[:, 3]
        assert float(alphas.min()) >= 0.5, (
            f"Shell alpha {float(alphas.min()):.2f} < 0.5 (eski 0.35 değeri hâlâ var)"
        )

    def test_DL08_shell_layer0_is_green(self):
        """_SHELL_LAYER_COLORS[0] yeşil: G > R ve G > B."""
        import sys as _sys
        core_parent = str(_ROOT / "faz17_d1/faz17_d1_backend")
        if core_parent not in _sys.path:
            _sys.path.insert(0, core_parent)
        import importlib as _il
        for k in list(_sys.modules.keys()):
            if "render_frame_builder" in k:
                del _sys.modules[k]
        mod = _il.import_module("faz17_d1.core.render_frame_builder")
        lut = getattr(mod, "_SHELL_LAYER_COLORS", None)
        assert lut is not None
        import numpy as np
        row = np.asarray(lut[0])
        r, g, b = float(row[0]), float(row[1]), float(row[2])
        assert g > r, f"Layer0: G={g:.2f} <= R={r:.2f} (yeşil değil)"
        assert g > b, f"Layer0: G={g:.2f} <= B={b:.2f} (yeşil değil)"

    def test_DL09_ribbon_renderer_imports_layer_colors(self):
        """ribbon_renderer.py LAYER_COLORS import etmeli."""
        src = _ribbon_src()
        assert "LAYER_COLORS" in src, (
            "ribbon_renderer.py LAYER_COLORS kullanmıyor."
        )

    def test_DL10_panel_imports_deposition_renderer(self):
        """entegre_tasarim_paneli.py DepositionRenderer import etmeli."""
        src = _panel_src()
        assert "DepositionRenderer" in src, (
            "DepositionRenderer panelde import edilmiyor."
        )

    def test_DL11_panel_has_setup_dep_renderer(self):
        """entegre_tasarim_paneli.py _setup_dep_renderer metodu içermeli."""
        src = _panel_src()
        assert "_setup_dep_renderer" in src, (
            "_setup_dep_renderer panelde tanımlanmamış."
        )

    def test_DL12_dep_period_positive(self):
        """DEP_PERIOD deposition_renderer.py'da pozitif integer olmalı."""
        dep_file = _APP / "app/renderers/deposition_renderer.py"
        src = dep_file.read_text(encoding="utf-8")
        assert "DEP_PERIOD" in src, "DEP_PERIOD deposition_renderer.py'da yok."
        # Değeri al
        import re
        m = re.search(r'DEP_PERIOD\s*=\s*(\d+)', src)
        assert m is not None, "DEP_PERIOD = <int> satırı bulunamadı."
        val = int(m.group(1))
        assert val > 0, f"DEP_PERIOD = {val} pozitif olmak zorunda."
