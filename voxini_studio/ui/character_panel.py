"""Charaktere tab: full CRUD for character profiles - name, description
(the identity block injected into every scene prompt) and reference image
thumbnails."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.ui import theme
from voxini_studio.ui.icons import icon

_THUMB_SIZE = QSize(96, 96)


class CharacterPanel(QWidget):
    def __init__(self, pm: ProjectManager, on_changed=None) -> None:
        super().__init__()
        self.pm = pm
        self.on_changed = on_changed or (lambda: None)
        self._selected_id: str | None = None

        outer = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.char_list = QListWidget()
        self.char_list.itemSelectionChanged.connect(self._on_select)
        left_layout.addWidget(self.char_list, 1)
        add_btn = QPushButton(" Neuer Charakter")
        add_btn.setIcon(icon("user-plus", theme.palette().text))
        add_btn.clicked.connect(self._add_character)
        left_layout.addWidget(add_btn)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Name"))
        self.name_edit = QLineEdit()
        right_layout.addWidget(self.name_edit)

        right_layout.addWidget(QLabel("Beschreibung (Identity-Block für Prompts)"))
        self.desc_edit = QPlainTextEdit()
        right_layout.addWidget(self.desc_edit, 1)

        right_layout.addWidget(QLabel("Referenzbilder"))
        self.ref_list = QListWidget()
        self.ref_list.setViewMode(QListWidget.IconMode)
        self.ref_list.setIconSize(_THUMB_SIZE)
        self.ref_list.setResizeMode(QListWidget.Adjust)
        self.ref_list.setFixedHeight(150)
        right_layout.addWidget(self.ref_list)

        ref_btns = QHBoxLayout()
        add_ref_btn = QPushButton(" Referenzbild hinzufügen...")
        add_ref_btn.setIcon(icon("image-plus", theme.palette().text))
        add_ref_btn.clicked.connect(self._add_reference)
        remove_ref_btn = QPushButton(" Ausgewähltes Referenzbild entfernen")
        remove_ref_btn.setProperty("role", "danger")
        remove_ref_btn.clicked.connect(self._remove_reference)
        ref_btns.addWidget(add_ref_btn)
        ref_btns.addWidget(remove_ref_btn)
        ref_btns.addStretch(1)
        right_layout.addLayout(ref_btns)

        action_row = QHBoxLayout()
        save_btn = QPushButton(" Speichern")
        save_btn.setIcon(icon("save", "#ffffff"))
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._save)
        delete_btn = QPushButton(" Charakter löschen")
        delete_btn.setProperty("role", "danger")
        delete_btn.clicked.connect(self._delete_character)
        action_row.addWidget(save_btn)
        action_row.addWidget(delete_btn)
        right_layout.addLayout(action_row)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter)

        self.refresh()

    # -- helpers ------------------------------------------------------
    def _require_project(self) -> bool:
        if self.pm.project is None:
            QMessageBox.warning(self, "Kein Projekt", "Bitte zuerst ein Projekt öffnen.")
            return False
        return True

    def refresh(self) -> None:
        self.char_list.clear()
        proj = self.pm.project
        if proj is None:
            self._clear_detail()
            return
        for char in proj.characters.values():
            item = QListWidgetItem(char.name)
            item.setData(Qt.UserRole, char.id)
            self.char_list.addItem(item)
        self._clear_detail()

    def _clear_detail(self) -> None:
        self._selected_id = None
        self.name_edit.setText("")
        self.desc_edit.setPlainText("")
        self.ref_list.clear()

    def _on_select(self) -> None:
        items = self.char_list.selectedItems()
        proj = self.pm.project
        if not items or proj is None:
            self._clear_detail()
            return
        char_id = items[0].data(Qt.UserRole)
        char = proj.characters.get(char_id)
        if char is None:
            return
        self._selected_id = char_id
        self.name_edit.setText(char.name)
        self.desc_edit.setPlainText(char.description)
        self.ref_list.clear()
        for rel_path in char.reference_image_paths:
            abs_path = self.pm.resolve(rel_path)
            item = QListWidgetItem(abs_path.name)
            item.setData(Qt.UserRole, rel_path)
            if abs_path.exists():
                pix = QPixmap(str(abs_path))
                if not pix.isNull():
                    item.setIcon(QIcon(pix.scaled(_THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            self.ref_list.addItem(item)

    # -- actions -----------------------------------------------------
    def _add_character(self) -> None:
        if not self._require_project():
            return
        char = self.pm.add_character("Neuer Charakter", "")
        self.refresh()
        for i in range(self.char_list.count()):
            if self.char_list.item(i).data(Qt.UserRole) == char.id:
                self.char_list.setCurrentRow(i)
                break
        self.on_changed()

    def _add_reference(self) -> None:
        if self._selected_id is None:
            QMessageBox.warning(self, "Kein Charakter", "Bitte zuerst einen Charakter auswählen.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Referenzbild wählen", "", "Bilder (*.png *.jpg *.jpeg)")
        if not path:
            return
        self.pm.import_character_reference(self._selected_id, path)
        self._on_select()
        self.on_changed()

    def _remove_reference(self) -> None:
        proj = self.pm.project
        if proj is None or self._selected_id is None:
            return
        char = proj.characters.get(self._selected_id)
        if char is None:
            return
        items = self.ref_list.selectedItems()
        if not items:
            QMessageBox.information(self, "Keine Auswahl", "Bitte zuerst ein Referenzbild in der Liste auswählen.")
            return
        rel_path = items[0].data(Qt.UserRole)
        if rel_path in char.reference_image_paths:
            char.reference_image_paths.remove(rel_path)
        self._on_select()
        self.on_changed()

    def _save(self) -> None:
        proj = self.pm.project
        if proj is None or self._selected_id is None:
            return
        char = proj.characters.get(self._selected_id)
        if char is None:
            return
        char.name = self.name_edit.text().strip() or char.name
        char.description = self.desc_edit.toPlainText()
        self.refresh()
        self.on_changed()

    def _delete_character(self) -> None:
        proj = self.pm.project
        if proj is None or self._selected_id is None:
            return
        char = proj.characters.get(self._selected_id)
        if char is None:
            return
        confirm = QMessageBox.question(
            self, "Charakter löschen", f"'{char.name}' wirklich löschen?"
        )
        if confirm != QMessageBox.Yes:
            return
        del proj.characters[self._selected_id]
        for scene in proj.scenes:
            if self._selected_id in scene.character_ids:
                scene.character_ids.remove(self._selected_id)
        self.refresh()
        self.on_changed()
