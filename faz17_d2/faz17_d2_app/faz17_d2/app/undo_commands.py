"""
app/undo_commands.py — Faz 25 Sprint 2: Merkezi Undo/Redo Komut Sınıfları
==========================================================================
MainWindow'daki tek QUndoStack üzerinde çalışan QUndoCommand hiyerarşisi.

Tasarım ilkeleri:
  * Komutlar canlı nesne referansı değil, SERİLEŞTİRİLMİŞ veri taşır
    (LayerSpec.to_dict() / satır dict'leri) — undo/redo deterministiktir.
  * Komutların redo()/undo() uygulamaları panellerin `_cmd_*` ilkelerini
    çağırır; bu ilkeler sinyalleri bastırır — komut kendini yeniden
    push edemez (sinyal döngüsü imkânsız).
  * Ardışık hücre/parametre düzenlemeleri mergeWith() ile birleşir
    (55→56→57→58 = tek Undo); eski değere dönülürse setObsolete(True)
    ile komut stack'ten düşürülür.
  * Denge çifti (±α) ve toplu işlemler tek komutta toplanır — tek Undo
    tüm işlemi geri alır.

Komutlar:
  KatmanDizilimPaneli : AddLayerCommand, DeleteLayerCommand,
                        EditLayerCommand, MoveLayerCommand,
                        ReplaceStackCommand
  EntegreTasarimPaneli: AddRowCommand, DeleteRowCommand,
                        EditRowCellCommand, ChangeMandrelCommand,
                        ChangeMachineSettingsCommand
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtGui import QUndoCommand


# Merge kimlikleri — aynı id'ye sahip ardışık komutlar mergeWith'e girer
_ID_EDIT_LAYER    = 0xA101   # KatmanDizilim hücre düzenleme
_ID_EDIT_ROW_CELL = 0xA102   # Entegre tablo hücre düzenleme
_ID_MANDREL       = 0xA103   # Entegre mandrel parametresi
_ID_MACHINE       = 0xA104   # Entegre fiber/sarma (makine) parametresi


# ═══════════════════════════════════════════════════════════════════════════
# KatmanDizilimPaneli komutları (backend LayerStack üzerinde)
# ═══════════════════════════════════════════════════════════════════════════

class AddLayerCommand(QUndoCommand):
    """Yığına bir veya daha fazla katman ekle (denge çifti = tek komut)."""

    def __init__(self, panel, layer_dicts: List[Dict[str, Any]],
                 index: Optional[int] = None, text: str = ""):
        super().__init__()
        self._panel = panel
        self._dicts = [dict(d) for d in layer_dicts]
        self._index = index            # None → ilk redo'da sona çözümlenir
        n = len(self._dicts)
        self.setText(text or (f"{n} katman ekle" if n > 1 else "katman ekle"))

    def redo(self) -> None:
        if self._index is None:
            self._index = self._panel._cmd_stack_len()
        self._panel._cmd_insert_layers(self._index, self._dicts)

    def undo(self) -> None:
        self._panel._cmd_remove_layers(self._index, len(self._dicts))


class DeleteLayerCommand(QUndoCommand):
    """Yığından tek katman sil (undo aynı konuma geri ekler)."""

    def __init__(self, panel, index: int, layer_dict: Dict[str, Any],
                 text: str = ""):
        super().__init__()
        self._panel = panel
        self._index = index
        self._dict = dict(layer_dict)
        self.setText(text or f"katman sil (satır {index + 1})")

    def redo(self) -> None:
        self._panel._cmd_remove_layers(self._index, 1)

    def undo(self) -> None:
        self._panel._cmd_insert_layers(self._index, [self._dict])


class EditLayerCommand(QUndoCommand):
    """Tek katman alanını düzenle. Aynı (satır, alan) ardışık
    düzenlemeleri mergeWith ile tek komuta birleşir."""

    def __init__(self, panel, index: int, field: str,
                 old_value: Any, new_value: Any):
        super().__init__()
        self._panel = panel
        self._index = index
        self._field = field
        self._old = old_value
        self._new = new_value
        self.setText(f"katman düzenle ({field}, satır {index + 1})")

    def id(self) -> int:
        return _ID_EDIT_LAYER

    def mergeWith(self, other: QUndoCommand) -> bool:
        if (not isinstance(other, EditLayerCommand)
                or other._panel is not self._panel
                or other._index != self._index
                or other._field != self._field):
            return False
        self._new = other._new
        if self._new == self._old:
            # Kullanıcı eski değere döndü — komut artık gereksiz
            self.setObsolete(True)
        return True

    def redo(self) -> None:
        self._panel._cmd_set_layer_field(self._index, self._field, self._new)

    def undo(self) -> None:
        self._panel._cmd_set_layer_field(self._index, self._field, self._old)


class MoveLayerCommand(QUndoCommand):
    """Katman sırasını değiştir (↑/↓ butonları)."""

    def __init__(self, panel, from_index: int, to_index: int):
        super().__init__()
        self._panel = panel
        self._from = from_index
        self._to = to_index
        self.setText(f"katman taşı ({from_index + 1} → {to_index + 1})")

    def redo(self) -> None:
        self._panel._cmd_move_layer(self._from, self._to)

    def undo(self) -> None:
        self._panel._cmd_move_layer(self._to, self._from)


class ReplaceStackCommand(QUndoCommand):
    """Tüm yığını değiştir — toplu işlemler için tek komut:
    tümünü temizle, optimizör reçetesi yükleme, toplu yapıştırma."""

    def __init__(self, panel, old_stack_dict: Dict[str, Any],
                 new_stack_dict: Dict[str, Any], text: str = ""):
        super().__init__()
        self._panel = panel
        self._old = dict(old_stack_dict)
        self._new = dict(new_stack_dict)
        self.setText(text or "yığını değiştir")

    def redo(self) -> None:
        self._panel._cmd_set_stack(self._new)

    def undo(self) -> None:
        self._panel._cmd_set_stack(self._old)


# ═══════════════════════════════════════════════════════════════════════════
# EntegreTasarimPaneli komutları (QTableWidget satırları + parametreler)
# ═══════════════════════════════════════════════════════════════════════════

class AddRowCommand(QUndoCommand):
    """Entegre panel katman tablosuna satır ekle."""

    def __init__(self, panel, row_dict: Dict[str, Any],
                 index: Optional[int] = None):
        super().__init__()
        self._panel = panel
        self._dict = dict(row_dict)
        self._index = index            # None → ilk redo'da sona çözümlenir
        self.setText(f"satır ekle ({row_dict.get('tip', '?')})")

    def redo(self) -> None:
        if self._index is None:
            self._index = self._panel._layer_table.rowCount()
        self._panel._cmd_insert_row(self._index, self._dict)

    def undo(self) -> None:
        self._panel._cmd_remove_row(self._index)


class DeleteRowCommand(QUndoCommand):
    """Entegre panel katman tablosundan satır sil."""

    def __init__(self, panel, index: int, row_dict: Dict[str, Any]):
        super().__init__()
        self._panel = panel
        self._index = index
        self._dict = dict(row_dict)
        self.setText(f"satır sil (satır {index + 1})")

    def redo(self) -> None:
        self._panel._cmd_remove_row(self._index)

    def undo(self) -> None:
        self._panel._cmd_insert_row(self._index, self._dict)


class EditRowCellCommand(QUndoCommand):
    """Entegre panel tablo hücresi düzenleme (mergeWith destekli)."""

    def __init__(self, panel, row: int, col: int,
                 old_text: str, new_text: str):
        super().__init__()
        self._panel = panel
        self._row = row
        self._col = col
        self._old = str(old_text)
        self._new = str(new_text)
        self.setText(f"hücre düzenle (satır {row + 1})")

    def id(self) -> int:
        return _ID_EDIT_ROW_CELL

    def mergeWith(self, other: QUndoCommand) -> bool:
        if (not isinstance(other, EditRowCellCommand)
                or other._panel is not self._panel
                or other._row != self._row
                or other._col != self._col):
            return False
        self._new = other._new
        if self._new == self._old:
            self.setObsolete(True)
        return True

    def redo(self) -> None:
        self._panel._cmd_set_cell(self._row, self._col, self._new)

    def undo(self) -> None:
        self._panel._cmd_set_cell(self._row, self._col, self._old)


class _ParamChangeCommand(QUndoCommand):
    """Ortak taban: adlandırılmış panel parametresi değişimi.

    Aynı parametrenin ardışık değişimleri (spinbox okuyla 55→56→57→58)
    mergeWith ile tek komuta birleşir — tek Undo tüm düzenlemeyi geri alır.
    """

    _MERGE_ID = 0  # alt sınıf belirler

    def __init__(self, panel, key: str, old_value: Any, new_value: Any,
                 label: str):
        super().__init__()
        self._panel = panel
        self._key = key
        self._old = old_value
        self._new = new_value
        self.setText(f"{label} değiştir ({key})")

    def id(self) -> int:
        return self._MERGE_ID

    def mergeWith(self, other: QUndoCommand) -> bool:
        if (not isinstance(other, type(self))
                or other._panel is not self._panel
                or other._key != self._key):
            return False
        self._new = other._new
        if self._new == self._old:
            self.setObsolete(True)
        return True

    def redo(self) -> None:
        self._panel._cmd_set_param(self._key, self._new)

    def undo(self) -> None:
        self._panel._cmd_set_param(self._key, self._old)


class ChangeMandrelCommand(_ParamChangeCommand):
    """Mandrel parametresi değişimi (tip, çap, uzunluk, konik, kubbe, H/R)."""

    _MERGE_ID = _ID_MANDREL

    def __init__(self, panel, key: str, old_value: Any, new_value: Any):
        super().__init__(panel, key, old_value, new_value, "mandrel")


class ChangeMachineSettingsCommand(_ParamChangeCommand):
    """Fiber/sarma makine ayarı değişimi (α, fitil, hız, RPM, μ, ...)."""

    _MERGE_ID = _ID_MACHINE

    def __init__(self, panel, key: str, old_value: Any, new_value: Any):
        super().__init__(panel, key, old_value, new_value, "makine ayarı")


__all__ = [
    "AddLayerCommand", "DeleteLayerCommand", "EditLayerCommand",
    "MoveLayerCommand", "ReplaceStackCommand",
    "AddRowCommand", "DeleteRowCommand", "EditRowCellCommand",
    "ChangeMandrelCommand", "ChangeMachineSettingsCommand",
]
