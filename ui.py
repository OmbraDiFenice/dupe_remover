#!/usr/bin/env python3
# pylint: disable=too-many-instance-attributes

from threading import Thread
import logging
import os
import shutil
import tempfile
import gi
import find_clones

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GObject  # pylint: disable=wrong-import-order


class AnalyzeJob(Thread):
    def __init__(self, app: find_clones.App, window: "AppWindow", path: str) -> None:
        super().__init__(daemon=True)
        self._app = app
        self._window = window
        self._path = path

    def run(self) -> None:
        self._window.emit("before_analysis_start")
        self._app.analyze_dir(self._path)
        self._window.emit("after_analysis_end")


class DupePathLabel(Gtk.EventBox):
    def __init__(self, file_path: str, dupe: find_clones.Duplicate, cache: find_clones.DeletionEntry|None) -> None:
        super().__init__()
        self.dupe = dupe
        self.file_path = file_path

        self.label = Gtk.Label()

        if cache is not None and file_path != cache.to_keep:
            self.label.set_markup(f"<s>{file_path}</s>")
        else:
            self.label.set_markup(file_path)

        self.add(self.label)
        self.show_all()


class DeletionConfirmDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, app: find_clones.App) -> None:
        super().__init__(title="Confirm", transient_for=parent)
        self.add_buttons(  # type: ignore
            Gtk.STOCK_NO, Gtk.ResponseType.NO,
            Gtk.STOCK_YES, Gtk.ResponseType.YES,
        )

        self.set_default_size(500, 600)

        title = Gtk.Label()
        title.set_markup("The following files are going to be deleted.\n\n<u><b>This action cannot be undone.</b></u>\n\nProceed?")

        summary = Gtk.Label()
        summary.set_markup(app._delete_queue.preview_delete_queue())

        actions_summary_container = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        actions_summary_container.add(summary)

        summary_title = Gtk.Label(label="Files to be removed:")

        box = self.get_content_area()
        box.pack_start(title, False, False, 5)
        box.pack_start(summary_title, False, False, 5)
        box.pack_start(actions_summary_container, True, True, 3)
        self.show_all()


class DupeSelectionPanel(Gtk.Box):
    def __init__(self, app: find_clones.App) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._app = app
        self._all_dupes: list[find_clones.Duplicate] = []
        self._current_dupe_idx = 0

        self._init_widgets()

    def _init_widgets(self) -> None:
        self._keep_selected_btn = Gtk.Button(label="Keep selected")
        self._keep_selected_btn.connect("clicked", self._on_keep_selected_clicked)
        self._keep_selected_btn.set_sensitive(False)

        self._reset_btn = Gtk.Button(label="Reset")
        self._reset_btn.connect("clicked", self._on_reset_clicked)
        self._reset_btn.set_sensitive(False)

        self._path_container = Gtk.FlowBox(orientation=Gtk.Orientation.HORIZONTAL, row_spacing=3, column_spacing=3)

        self._image = Gtk.Image(vexpand=False, hexpand=False)

        self._navigation = Gtk.HeaderBar()

        self._prev_dupe_btn = Gtk.Button(label="< Previous")
        self._prev_dupe_btn.connect("clicked", lambda _: self._prev_dupe())
        self._prev_dupe_btn.set_sensitive(False)

        self._next_dupe_btn = Gtk.Button(label="Next >")
        self._next_dupe_btn.connect("clicked", lambda _: self._next_dupe())
        self._next_dupe_btn.set_sensitive(False)

        self._navigation.pack_start(self._prev_dupe_btn)
        self._navigation.pack_end(self._next_dupe_btn)

        self.pack_start(self._navigation, True, False, 0)
        self.pack_start(self._image, True, True, 0)
        self.pack_start(self._path_container, True, False, 0)
        self.pack_start(self._keep_selected_btn, True, False, 3)
        self.pack_start(self._reset_btn, True, False, 3)

    def update(self) -> None:
        self._all_dupes = self._app.get_all_duplicates()

        if len(self._all_dupes) == 0:
            self._navigation.set_title("No duplicates found")
        else:
            self._navigation.set_title(f"{self._current_dupe_idx+1}/{len(self._all_dupes)}")
        self._keep_selected_btn.set_sensitive(len(self._all_dupes) > 0)
        self._reset_btn.set_sensitive(len(self._all_dupes) > 0)
        self._prev_dupe_btn.set_sensitive(self._current_dupe_idx > 0)
        self._next_dupe_btn.set_sensitive(self._current_dupe_idx < len(self._all_dupes) - 1)

        if len(self._all_dupes) > 0:
            self._show_dupe(self._all_dupes[self._current_dupe_idx])

    def _on_reset_clicked(self, _: Gtk.Button) -> None:
        current_dupe = self._all_dupes[self._current_dupe_idx]
        self._app.remove_from_deletion_queue(current_dupe)
        self._show_dupe(current_dupe)

    def _on_keep_selected_clicked(self, _: Gtk.Button) -> None:
        selected = self._path_container.get_selected_children()
        if len(selected) == 0:
            return
        child = selected.pop().get_child()
        assert child is not None and isinstance(child, DupePathLabel)
        selected_path = child.file_path

        self._keep_selected(child.dupe, selected_path)

    def _on_path_entry_clicked(self, label: DupePathLabel, event: Gdk.EventButton) -> None:
        if event.button == 1 and event.type == Gdk.EventType._2BUTTON_PRESS:  # pylint: disable=protected-access
            self._keep_selected(label.dupe, label.file_path)

    def _keep_selected(self, dupe: find_clones.Duplicate, selected_path: str) -> None:
        self._app.queue_for_deletion(dupe, selected_path)
        self._reset_btn.set_sensitive(self._app.get_queued_deletion_entry_for(dupe) is not None)
        self._set_path_list(dupe)
        self._next_dupe()

    def _show_dupe(self, dupe: find_clones.Duplicate) -> None:
        self._navigation.set_title(f"{self._current_dupe_idx+1}/{len(self._all_dupes)}")

        image_path = dupe.files[0] if os.path.exists(dupe.files[0]) else "resources/broken_image.png"
        image = GdkPixbuf.Pixbuf.new_from_file_at_scale(image_path, 500, 500, True)

        self._image.set_from_pixbuf(image)

        self._reset_btn.set_sensitive(self._app.get_queued_deletion_entry_for(dupe) is not None)
        self._set_path_list(dupe)

    def _set_path_list(self, dupe: find_clones.Duplicate) -> None:
        self._empty_path_list()

        queued_deletion = self._app.get_queued_deletion_entry_for(dupe)

        for path in dupe.files:
            path_entry = DupePathLabel(path, dupe, queued_deletion)
            path_entry.connect("button-press-event", self._on_path_entry_clicked)
            self._path_container.add(path_entry)

    def _empty_path_list(self) -> None:
        for child in self._path_container.get_children():
            self._path_container.remove(child)

    def _prev_dupe(self) -> None:
        if self._current_dupe_idx <= 0:
            return

        self._current_dupe_idx -= 1
        self._prev_dupe_btn.set_sensitive(self._current_dupe_idx > 0)
        self._next_dupe_btn.set_sensitive(self._current_dupe_idx < len(self._all_dupes) - 1)

        dupe = self._all_dupes[self._current_dupe_idx]
        self._show_dupe(dupe)

    def _next_dupe(self) -> None:
        if self._current_dupe_idx >= len(self._all_dupes) - 1:
            return

        self._current_dupe_idx += 1
        self._prev_dupe_btn.set_sensitive(self._current_dupe_idx > 0)
        self._next_dupe_btn.set_sensitive(self._current_dupe_idx < len(self._all_dupes) - 1)

        dupe = self._all_dupes[self._current_dupe_idx]
        self._show_dupe(dupe)


class AppWindow(Gtk.Window):
    __gsignals__ = {
        "before_analysis_start": (GObject.SignalFlags.RUN_FIRST, None, ()), 
        "after_analysis_end": (GObject.SignalFlags.RUN_FIRST, None, ()), 
    }

    def __init__(self, app: find_clones.App) -> None:
        super().__init__(title="Duplicate Remover")

        self._app = app
        self._needs_select_dir = True  # Indicates that we need to select a new session when saving
                                       # This is only required the first time the UI starts since App
                                       # will be initialized with a temporary directory

        self._dupes_list: list[list[str]] = []
        self._current_dupe_idx = -1

        self._toolbar = Gtk.Box()

        self._analyze_btn = Gtk.Button(label="Analyze directory")
        self._analyze_btn.connect("clicked", self.on_analyze_dir_clicked)

        self._delete_duplicates_btn = Gtk.Button(label="Delete duplicates")
        self._delete_duplicates_btn.connect("clicked", self.on_delete_duplicates_clicked)

        self._save_session_btn = Gtk.Button(label="Save session")
        self._save_session_btn.connect("clicked", self.on_save_session_clicked)

        self._load_session_btn = Gtk.Button(label="Load session")
        self._load_session_btn.connect("clicked", self.on_load_session_clicked)

        self._toolbar.pack_start(self._analyze_btn, False, False, 2)
        self._toolbar.pack_start(self._delete_duplicates_btn, False, False, 2)
        self._toolbar.pack_start(self._save_session_btn, False, False, 2)
        self._toolbar.pack_start(self._load_session_btn, False, False, 2)

        self._status_bar = Gtk.Statusbar()

        self._session_name = Gtk.Label(label=self._app.session.session_dir)
        self._status_bar.pack_start(self._session_name, False, False, 5)

        self._selection_panel = DupeSelectionPanel(self._app)

        widgets = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        widgets.pack_start(self._toolbar, False, False, 5)
        widgets.pack_start(self._selection_panel, True, True, 5)
        widgets.pack_start(self._status_bar, True, True, 5)

        self.add(widgets)

    def on_analyze_dir_clicked(self, _: Gtk.Button) -> None:
        directory = self._get_directory()
        if directory is None:
            return

        analyze_job = AnalyzeJob(self._app, self, directory)
        analyze_job.start()

    def do_before_analysis_start(self) -> None:
        self._analyze_btn.set_sensitive(False)
        self._status_bar.push(0, "Analyzing...")

    def do_after_analysis_end(self) -> None:
        self._status_bar.pop(0)
        self._status_bar.push(0, "Done")
        self._analyze_btn.set_sensitive(True)
        self._app.clear_deletion_queue()
        self._selection_panel.update()

    def on_delete_duplicates_clicked(self, _: Gtk.Button) -> None:
        dialog = DeletionConfirmDialog(self, self._app)
        response = dialog.run()  # type: ignore
        dialog.destroy()

        if response == Gtk.ResponseType.YES:
            self._app.do_delete_queued_files()
            self._selection_panel.update()

    def on_save_session_clicked(self, _: Gtk.Button) -> None:
        if self._needs_select_dir:
            session_dir = self._get_directory()
            if session_dir is None:
                return

            if os.path.exists(session_dir):
                dialog = Gtk.MessageDialog(
                    parent=self,
                    message_type=Gtk.MessageType.WARNING,
                    buttons=Gtk.ButtonsType.YES_NO,
                    text=f"Session {session_dir} already exists. Overwrite?",
                )
                overwrite = dialog.run()  # type: ignore
                dialog.destroy()
                if overwrite != Gtk.ResponseType.YES:
                    return

            shutil.copytree(self._app.session.session_dir, session_dir, dirs_exist_ok=True)
            self._app.load_session(find_clones.Session(session_dir))
            self._needs_select_dir = False

        self._app.save_session()
        self._session_name.set_text(self._app.session.session_dir)

    def on_load_session_clicked(self, _: Gtk.Button) -> None:
        session_dir = self._get_directory()
        if session_dir is None:
            return

        new_session = find_clones.Session(session_dir)
        self._app.load_session(new_session)
        self._needs_select_dir = False
        self._session_name.set_text(self._app.session.session_dir)
        self._selection_panel.update()

    def _get_directory(self) -> str | None:
        directory = None

        dialog = Gtk.FileChooserDialog(
            title="Please choose a folder",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(  # type: ignore
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Select", Gtk.ResponseType.OK
        )
        dialog.set_default_size(800, 400)

        response = dialog.run()  # type: ignore
        if response == Gtk.ResponseType.OK:
            directory = dialog.get_filename()

        dialog.destroy()

        return directory

    def _get_file(self, default_name: str|None = None) -> str | None:
        file = None

        dialog = Gtk.FileChooserDialog(
            title="Please choose a file",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        if default_name is not None:
            dialog.set_current_name(default_name)
        dialog.add_buttons(  # type: ignore
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Select", Gtk.ResponseType.OK
        )
        dialog.set_default_size(800, 400)

        response = dialog.run()  # type: ignore
        if response == Gtk.ResponseType.OK:
            file = dialog.get_filename()

        dialog.destroy()

        return file


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    session = find_clones.Session(tempfile.mkdtemp())
    app = find_clones.App(session)

    win = AppWindow(app)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()

    shutil.rmtree(session.session_dir)


if __name__ == "__main__":
    main()
