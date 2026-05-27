"""
panels/recipe_editor.py — Process Recipe CRUD + Validation
==============================================================
Edit/save/load Recipe objects to/from RecipeDB.
Inline validation feedback (red border on invalid fields).
"""
from __future__ import annotations
from typing import Optional, List
from PySide6.QtCore import Qt, Slot, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QPushButton,
    QGroupBox, QPlainTextEdit, QListWidget, QListWidgetItem, QSplitter,
    QMessageBox, QFrame, QGridLayout)

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from backend.persistence.recipe_db import RecipeDB, Recipe

from ..themes.dark_industrial import COLOR


class RecipeEditor(QWidget):
    """Recipe CRUD interface."""

    recipeSaved = Signal(object)
    recipeLoaded = Signal(object)

    def __init__(self, recipe_db: Optional[RecipeDB] = None, parent=None):
        super().__init__(parent)
        self._db = recipe_db
        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        title = QLabel("Process Recipe Editor")
        title.setProperty("role", "header")
        outer.addWidget(title)

        # Split: list on left, editor on right
        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, stretch=1)

        # Left: list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(QLabel("Saved Recipes"))
        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_list_clicked)
        left_layout.addWidget(self._list)
        list_btn_row = QHBoxLayout()
        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._on_new)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._on_delete)
        list_btn_row.addWidget(new_btn)
        list_btn_row.addWidget(del_btn)
        left_layout.addLayout(list_btn_row)
        splitter.addWidget(left)

        # Right: editor form
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        # Identity
        id_box = QGroupBox("Recipe Identification")
        id_grid = QGridLayout(id_box)
        id_grid.addWidget(QLabel("Recipe ID:"), 0, 0)
        self._id_edit = QLineEdit(); self._id_edit.setPlaceholderText("EP120_T700_v1")
        id_grid.addWidget(self._id_edit, 0, 1)
        id_grid.addWidget(QLabel("Version:"), 0, 2)
        self._ver_spin = QSpinBox(); self._ver_spin.setRange(1, 9999)
        id_grid.addWidget(self._ver_spin, 0, 3)
        id_grid.addWidget(QLabel("Name:"), 1, 0)
        self._name_edit = QLineEdit()
        id_grid.addWidget(self._name_edit, 1, 1, 1, 3)
        right_layout.addWidget(id_box)

        # Materials
        mat_box = QGroupBox("Materials")
        mat_grid = QGridLayout(mat_box)
        mat_grid.addWidget(QLabel("Resin system:"), 0, 0)
        self._resin_combo = QComboBox()
        self._resin_combo.addItems(
            ["EPON828_Ancamine2049", "VinylEster_Derakane510", "Custom"])
        mat_grid.addWidget(self._resin_combo, 0, 1)
        mat_grid.addWidget(QLabel("Fiber:"), 1, 0)
        self._fiber_combo = QComboBox()
        self._fiber_combo.addItems(
            ["Toray T700SC-12K", "Toray T800SC-24K", "Glass E-glass-2400", "Custom"])
        mat_grid.addWidget(self._fiber_combo, 1, 1)
        right_layout.addWidget(mat_box)

        # Winding
        wind_box = QGroupBox("Winding Parameters")
        wind_grid = QGridLayout(wind_box)
        wind_grid.addWidget(QLabel("Winding angle α:"), 0, 0)
        self._alpha_spin = QDoubleSpinBox()
        self._alpha_spin.setRange(1.0, 89.0); self._alpha_spin.setDecimals(2)
        self._alpha_spin.setSuffix(" °"); self._alpha_spin.setValue(10.17)
        wind_grid.addWidget(self._alpha_spin, 0, 1)

        wind_grid.addWidget(QLabel("Layers:"), 0, 2)
        self._layers_spin = QSpinBox()
        self._layers_spin.setRange(1, 50); self._layers_spin.setValue(8)
        wind_grid.addWidget(self._layers_spin, 0, 3)

        wind_grid.addWidget(QLabel("Tension:"), 1, 0)
        self._tension_spin = QDoubleSpinBox()
        self._tension_spin.setRange(3.0, 38.0); self._tension_spin.setDecimals(2)
        self._tension_spin.setSuffix(" N"); self._tension_spin.setValue(15.0)
        wind_grid.addWidget(self._tension_spin, 1, 1)

        wind_grid.addWidget(QLabel("Feed rate:"), 1, 2)
        self._feed_spin = QDoubleSpinBox()
        self._feed_spin.setRange(10.0, 200.0); self._feed_spin.setDecimals(1)
        self._feed_spin.setSuffix(" mm/s"); self._feed_spin.setValue(100.0)
        wind_grid.addWidget(self._feed_spin, 1, 3)
        right_layout.addWidget(wind_box)

        # Cure cycle
        cure_box = QGroupBox("Cure Cycle")
        cure_grid = QGridLayout(cure_box)
        cure_grid.addWidget(QLabel("Cure temperature:"), 0, 0)
        self._cureT_spin = QDoubleSpinBox()
        self._cureT_spin.setRange(20.0, 200.0); self._cureT_spin.setDecimals(1)
        self._cureT_spin.setSuffix(" °C"); self._cureT_spin.setValue(120.0)
        cure_grid.addWidget(self._cureT_spin, 0, 1)

        cure_grid.addWidget(QLabel("Cure duration:"), 0, 2)
        self._cureH_spin = QDoubleSpinBox()
        self._cureH_spin.setRange(0.5, 48.0); self._cureH_spin.setDecimals(1)
        self._cureH_spin.setSuffix(" h"); self._cureH_spin.setValue(8.0)
        cure_grid.addWidget(self._cureH_spin, 0, 3)
        right_layout.addWidget(cure_box)

        # Quality targets
        q_box = QGroupBox("Quality Targets")
        q_grid = QGridLayout(q_box)
        q_grid.addWidget(QLabel("Vf target:"), 0, 0)
        self._vf_spin = QDoubleSpinBox()
        self._vf_spin.setRange(0.30, 0.75); self._vf_spin.setDecimals(3)
        self._vf_spin.setSingleStep(0.01); self._vf_spin.setValue(0.55)
        q_grid.addWidget(self._vf_spin, 0, 1)

        q_grid.addWidget(QLabel("Void max:"), 0, 2)
        self._void_spin = QDoubleSpinBox()
        self._void_spin.setRange(0.1, 10.0); self._void_spin.setDecimals(2)
        self._void_spin.setSuffix(" %"); self._void_spin.setValue(2.5)
        q_grid.addWidget(self._void_spin, 0, 3)
        right_layout.addWidget(q_box)

        # Notes
        notes_box = QGroupBox("Notes")
        notes_layout = QVBoxLayout(notes_box)
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText("Operator notes, batch context, …")
        self._notes_edit.setMaximumHeight(80)
        notes_layout.addWidget(self._notes_edit)
        right_layout.addWidget(notes_box)

        # Validation feedback
        self._validation_lbl = QLabel("")
        self._validation_lbl.setWordWrap(True)
        right_layout.addWidget(self._validation_lbl)

        # Save row
        save_row = QHBoxLayout()
        save_row.addStretch()
        validate_btn = QPushButton("Validate")
        validate_btn.clicked.connect(self._on_validate)
        save_row.addWidget(validate_btn)
        self._save_btn = QPushButton("Save")
        self._save_btn.setProperty("role", "primary")
        self._save_btn.clicked.connect(self._on_save)
        save_row.addWidget(self._save_btn)
        right_layout.addLayout(save_row)
        right_layout.addStretch()

        splitter.addWidget(right)
        splitter.setSizes([200, 700])

    def _current_recipe(self) -> Recipe:
        return Recipe(
            recipe_id=self._id_edit.text().strip(),
            version=self._ver_spin.value(),
            name=self._name_edit.text().strip(),
            resin_system=self._resin_combo.currentText(),
            fiber=self._fiber_combo.currentText(),
            alpha_deg=self._alpha_spin.value(),
            n_layers=self._layers_spin.value(),
            tension_N=self._tension_spin.value(),
            feed_mm_s=self._feed_spin.value(),
            cure_T_C=self._cureT_spin.value(),
            cure_h=self._cureH_spin.value(),
            Vf_target=self._vf_spin.value(),
            void_max_pct=self._void_spin.value(),
            notes=self._notes_edit.toPlainText())

    def _populate_from(self, r: Recipe):
        self._id_edit.setText(r.recipe_id)
        self._ver_spin.setValue(r.version)
        self._name_edit.setText(r.name)
        idx = self._resin_combo.findText(r.resin_system)
        self._resin_combo.setCurrentIndex(idx if idx >= 0 else
            self._resin_combo.count() - 1)
        idx = self._fiber_combo.findText(r.fiber)
        self._fiber_combo.setCurrentIndex(idx if idx >= 0 else
            self._fiber_combo.count() - 1)
        self._alpha_spin.setValue(r.alpha_deg)
        self._layers_spin.setValue(r.n_layers)
        self._tension_spin.setValue(r.tension_N)
        self._feed_spin.setValue(r.feed_mm_s)
        self._cureT_spin.setValue(r.cure_T_C)
        self._cureH_spin.setValue(r.cure_h)
        self._vf_spin.setValue(r.Vf_target)
        self._void_spin.setValue(r.void_max_pct)
        self._notes_edit.setPlainText(r.notes)

    def _on_validate(self):
        r = self._current_recipe()
        errs = r.validate()
        if errs:
            self._validation_lbl.setText(
                "Validation errors: " + ", ".join(errs))
            self._validation_lbl.setProperty("status", "crit")
        else:
            self._validation_lbl.setText(f"✓ Recipe valid. Checksum: {r.checksum()}")
            self._validation_lbl.setProperty("status", "ok")
        self._validation_lbl.style().unpolish(self._validation_lbl)
        self._validation_lbl.style().polish(self._validation_lbl)

    def _on_save(self):
        if self._db is None:
            QMessageBox.warning(self, "No DB", "Recipe DB not connected.")
            return
        r = self._current_recipe()
        errs = r.validate()
        if errs:
            QMessageBox.warning(self, "Invalid recipe", "\n".join(errs))
            return
        if not r.recipe_id:
            QMessageBox.warning(self, "Missing ID", "Recipe ID is required.")
            return
        if self._db.save(r):
            self._refresh_list()
            self.recipeSaved.emit(r)
            self._validation_lbl.setText("✓ Saved.")
            self._validation_lbl.setProperty("status", "ok")
            self._validation_lbl.style().unpolish(self._validation_lbl)
            self._validation_lbl.style().polish(self._validation_lbl)
        else:
            QMessageBox.critical(self, "Save failed", "Save to DB failed.")

    def _on_new(self):
        self._id_edit.clear()
        self._name_edit.clear()
        self._ver_spin.setValue(1)
        self._notes_edit.clear()

    def _on_delete(self):
        cur = self._list.currentItem()
        if cur is None or self._db is None: return
        rid = cur.data(Qt.UserRole)
        reply = QMessageBox.question(self, "Delete",
            f"Delete all versions of recipe '{rid}'?")
        if reply == QMessageBox.Yes:
            self._db.delete(rid)
            self._refresh_list()

    def _on_list_clicked(self, item):
        if self._db is None: return
        rid = item.data(Qt.UserRole)
        r = self._db.load(rid)
        if r is not None:
            self._populate_from(r)
            self.recipeLoaded.emit(r)

    def _refresh_list(self):
        self._list.clear()
        if self._db is None: return
        for r_meta in self._db.list_recipes():
            text = f"{r_meta['recipe_id']} (v{r_meta['version']})"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, r_meta['recipe_id'])
            self._list.addItem(item)
