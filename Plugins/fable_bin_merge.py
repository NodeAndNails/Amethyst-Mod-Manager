from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk

from Utils.modlist import read_modlist
from Utils.portal_filechooser import pick_file

if TYPE_CHECKING:
    from Games.base_game import BaseGame

from gui.theme import (
    ACCENT,
    ACCENT_HOV,
    BG_DEEP,
    BG_HEADER,
    BG_PANEL,
    BORDER,
    FONT_BOLD,
    FONT_NORMAL,
    FONT_SMALL,
    TEXT_DIM,
    TEXT_MAIN,
    TEXT_WHITE,
)


class FableBinMergeWizard(ctk.CTkFrame):
    def __init__(self, parent, game: "BaseGame", log_fn=None, *, on_close=None, **kw):
        super().__init__(parent, fg_color=BG_DEEP, corner_radius=0)
        self._game = game
        self._log = log_fn or (lambda _: None)
        self._on_close_cb = on_close or (lambda: None)
        self._game_subpath = kw.get("game_subpath", "")
        self._bin_names = kw.get("bin_names", [])
        self._step = 0

        # Title Bar
        title_bar = ctk.CTkFrame(self, fg_color=BG_HEADER, corner_radius=0, height=40)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)

        ctk.CTkLabel(
            title_bar,
            text=f"Bin Merge Wizard — {game.name}",
            font=FONT_BOLD,
            text_color=TEXT_MAIN,
            anchor="w",
        ).pack(side="left", padx=12, pady=8)

        ctk.CTkButton(
            title_bar,
            text="✕",
            width=32,
            height=32,
            font=FONT_BOLD,
            fg_color="transparent",
            hover_color=BG_PANEL,
            text_color=TEXT_MAIN,
            command=self._on_cancel,
        ).pack(side="right", padx=4, pady=4)

        # Body Frame
        self._body = ctk.CTkFrame(self, fg_color=BG_DEEP)
        self._body.pack(fill="both", expand=True, padx=20, pady=20)

        # Footer Frame
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(4, 16))

        self._back_btn = ctk.CTkButton(
            footer,
            text="← Back",
            width=100,
            height=32,
            font=FONT_NORMAL,
            fg_color=BG_PANEL,
            hover_color=BG_HEADER,
            text_color=TEXT_MAIN,
            command=self._go_back,
        )
        self._back_btn.pack(side="left")

        self._next_btn = ctk.CTkButton(
            footer,
            text="Next →",
            width=100,
            height=32,
            font=FONT_BOLD,
            fg_color=ACCENT,
            hover_color=ACCENT_HOV,
            text_color="white",
            command=self._go_next,
        )
        self._next_btn.pack(side="right")

        self._show_step()

    def _on_cancel(self):
        sandbox_dir = self._game.get_profile_root() / "fable_working_dir"
        if sandbox_dir.exists():
            try:
                shutil.rmtree(sandbox_dir)
                self._log("Merge Wizard: Cleaned up sandbox directory.")
            except Exception:
                pass
        self._on_close_cb()

    def _clear_body(self):
        """Destroys all current step content in the body frame."""
        for w in self._body.winfo_children():
            w.destroy()

    def _show_step_0(self):
        """Step 1: Configure Merge Tool."""
        self._clear_body()

        ctk.CTkLabel(
            self._body,
            text="Step 1: Configure Merge Tool",
            font=FONT_BOLD,
            text_color=TEXT_MAIN,
        ).pack(pady=(0, 10))

        ctk.CTkLabel(
            self._body,
            text=(
                "Fable uses compiled .bin files for gameplay definitions. "
                "To merge multiple mods that change these files, you need "
                "a merge tool like EgoCore or Fable Explorer. "
                "Select your preferred tool and locate its executable."
            ),
            font=FONT_NORMAL,
            text_color=TEXT_DIM,
            wraplength=500,
            justify="center",
        ).pack(pady=(0, 20))

        # Status Indicator
        status_frame = ctk.CTkFrame(self._body, fg_color=BG_PANEL, corner_radius=6)
        status_frame.pack(fill="x", padx=40, pady=(0, 20))

        has_merge_tool = getattr(self._game, "has_merge_tool", False)
        merge_tool_path = getattr(self._game, "_merge_tool_path", None)

        if has_merge_tool:
            status_text = "Merge tool is configured"
            status_color = "#6bc76b"  # Green
            path_text = str(merge_tool_path)
            icon = "✓"
        else:
            status_text = "Merge tool is not configured"
            status_color = "#e5a04a"  # Orange
            path_text = "No path set"
            icon = "!"

        ctk.CTkLabel(
            status_frame,
            text=f"{icon} {status_text}",
            font=FONT_BOLD,
            text_color=status_color,
        ).pack(pady=(10, 2))
        ctk.CTkLabel(
            status_frame,
            text=path_text,
            font=FONT_SMALL,
            text_color=TEXT_DIM,
            wraplength=400,
        ).pack(pady=(0, 10))

        # Tool Selection Dropdown
        tool_frame = ctk.CTkFrame(self._body, fg_color="transparent")
        tool_frame.pack(fill="x", padx=40, pady=(0, 10))

        ctk.CTkLabel(
            tool_frame, text="Tool Type:", font=FONT_BOLD, text_color=TEXT_MAIN
        ).pack(side="left", padx=(0, 10))

        self._tool_type_var = ctk.StringVar(
            value=getattr(self._game, "_merge_tool_type", "egocore")
        )
        tool_dropdown = ctk.CTkOptionMenu(
            tool_frame,
            values=["egocore", "fable_explorer"],
            variable=self._tool_type_var,
            command=self._on_tool_type_change,
            fg_color=BG_HEADER,
            button_color=BG_HEADER,
            button_hover_color=BG_PANEL,
        )
        tool_dropdown.pack(side="left")

        # Selection Row
        path_frame = ctk.CTkFrame(self._body, fg_color="transparent")
        path_frame.pack(fill="x", padx=40)

        self._path_entry = ctk.CTkEntry(
            path_frame,
            placeholder_text="Path to executable...",
            font=FONT_NORMAL,
            fg_color=BG_PANEL,
            text_color=TEXT_MAIN,
            border_color=BORDER,
            height=32,
        )
        self._path_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        if has_merge_tool:
            self._path_entry.insert(0, str(merge_tool_path))

        ctk.CTkButton(
            path_frame,
            text="Browse...",
            width=100,
            height=32,
            font=FONT_BOLD,
            fg_color=BG_HEADER,
            hover_color=BG_PANEL,
            text_color=TEXT_MAIN,
            command=self._on_browse_tool,
        ).pack(side="right")

        ctk.CTkButton(
            self._body,
            text="Configure Later / Skip",
            width=150,
            height=26,
            font=FONT_SMALL,
            fg_color="transparent",
            text_color=TEXT_DIM,
            hover_color=BG_PANEL,
            command=self._on_cancel,
        ).pack(side="bottom", pady=(20, 0))

    def _on_tool_type_change(self, tool_type: str):
        set_path_fn = getattr(self._game, "set_merge_tool", None)
        if set_path_fn:
            set_path_fn(getattr(self._game, "_merge_tool_path", None), tool_type)

    def _on_browse_tool(self):
        def _on_picked(path: Path | None):
            if path:
                set_path_fn = getattr(self._game, "set_merge_tool", None)
                if set_path_fn:
                    set_path_fn(path, self._tool_type_var.get())
                self._show_step()  # Refresh buttons and status

        pick_file("Select Merge Tool Executable", _on_picked)

    def _show_step_1(self):
        """Step 2: Conflict Summary."""
        self._clear_body()
        ctk.CTkLabel(
            self._body,
            text="Step 2: Conflict Summary",
            font=FONT_BOLD,
            text_color=TEXT_MAIN,
        ).pack(pady=(0, 10))

        conflicts = self._detect_bin_conflicts()
        if not conflicts:
            ctk.CTkLabel(
                self._body,
                text=(
                    "No conflicts detected among .bin/.fmp files.\n"
                    "Per-entry merging is not required."
                ),
                font=FONT_NORMAL,
                text_color="#6bc76b",
            ).pack(pady=40)
        else:
            self._render_conflict_list(conflicts)

            # Sandbox preparation
            ctk.CTkLabel(
                self._body,
                text=(
                    "Mod merging must be done in a separate working directory "
                    "to prevent corruption of your mod staging files."
                ),
                font=FONT_SMALL,
                text_color=TEXT_DIM,
                wraplength=520,
            ).pack(pady=(20, 10))

            self._sandbox_btn = ctk.CTkButton(
                self._body,
                text="1. Prepare Sandbox",
                font=FONT_BOLD,
                fg_color=BG_HEADER,
                hover_color=BG_PANEL,
                command=self._prepare_sandbox,
            )
            self._sandbox_btn.pack()

    def _prepare_sandbox(self):
        """Creates a isolated workspace for merging to protect mod staging files."""
        self._log("Merge Wizard: Preparing sandbox...")
        sandbox_dir = self._game.get_profile_root() / "fable_working_dir"
        if sandbox_dir.exists():
            try:
                shutil.rmtree(sandbox_dir)
            except Exception as e:
                self._log(f"Merge Wizard: ERROR - Could not clear old sandbox: {e}")
                return
        sandbox_dir.mkdir(parents=True)
        fmps_dir = sandbox_dir / "fmps"
        fmps_dir.mkdir()

        game_path = self._game.get_game_path()
        if not game_path:
            return
        compiled_defs = game_path / self._game_subpath

        # 1. Copy base bins from the game (which are the deployed files)
        for name in self._bin_names:
            if name.endswith(".bin"):
                src = compiled_defs / name
                if src.is_file():
                    shutil.copy2(src, sandbox_dir / name)

        # 2. Collect FMPs from conflicting mods
        conflicts = self._detect_bin_conflicts()
        staging = self._game.get_effective_mod_staging_path()
        unique_mods = set()
        for mods in conflicts.values():
            unique_mods.update(mods)

        for mod_name in unique_mods:
            mod_root = staging / mod_name
            for p in mod_root.rglob("*.fmp"):
                dest_name = f"{mod_name}_{p.name}"
                shutil.copy2(p, fmps_dir / dest_name)

        self._log("Merge Wizard: Sandbox ready at fable_working_dir.")
        self._sandbox_btn.configure(text="✓ Sandbox Ready", state="disabled")

    def _show_step_2(self):
        """Step 3: Launch Merge Tool."""
        self._clear_body()

        tool_type = getattr(self._game, "_merge_tool_type", "egocore")
        tool_name = "EgoCore" if tool_type == "egocore" else "Fable Explorer"

        ctk.CTkLabel(
            self._body,
            text=f"Step 3: Launch {tool_name}",
            font=FONT_BOLD,
            text_color=TEXT_MAIN,
        ).pack(pady=(0, 10))

        if tool_type == "egocore":
            instructions = (
                "1. Click 'Launch' to open EgoCore under Proton.\n"
                "2. It will open in the sandbox directory.\n"
                "3. Open 'names.bin' first, then 'game.bin' from the sandbox root.\n"
                "4. Load '.fmp' files from the sandbox 'fmps' folder.\n"
                "5. Once finished, save your changes and close EgoCore.\n"
                "6. Amethyst will detect the close and apply the changes."
            )
        else:
            instructions = (
                "1. Click 'Launch' to open Fable Explorer under Proton.\n"
                "2. It will open in the sandbox directory.\n"
                "3. Load 'names.bin' and 'game.bin' from the sandbox root.\n"
                "4. Use 'Actions > Load FMP' to load files from sandbox 'fmps'"
                " folder.\n"
                "5. Once finished, save your changes and close Fable Explorer.\n"
                "6. Amethyst will detect the close and apply the changes."
            )

        ctk.CTkLabel(
            self._body,
            text=instructions,
            font=FONT_NORMAL,
            text_color=TEXT_MAIN,
            justify="left",
            wraplength=520,
        ).pack(pady=(0, 15))

        # Sandbox path info box
        sandbox_frame = ctk.CTkFrame(self._body, fg_color=BG_PANEL, corner_radius=6)
        sandbox_frame.pack(fill="x", padx=20, pady=(0, 20))

        sandbox_dir = self._game.get_profile_root() / "fable_working_dir"
        ctk.CTkLabel(
            sandbox_frame,
            text="Merge Sandbox Directory:",
            font=FONT_SMALL,
            text_color=TEXT_DIM,
        ).pack(pady=(8, 2), padx=10)

        sandbox_path_lbl = ctk.CTkLabel(
            sandbox_frame,
            text=str(sandbox_dir),
            font=FONT_SMALL,
            text_color="#6bc76b",
        )
        sandbox_path_lbl.pack(pady=(0, 8))

        # Launch Button
        self._launch_btn = ctk.CTkButton(
            self._body,
            text=f"Launch {tool_name}",
            font=FONT_BOLD,
            fg_color=ACCENT,
            hover_color=ACCENT_HOV,
            text_color=TEXT_WHITE,
            height=40,
            command=self._do_launch,
        )
        self._launch_btn.pack(pady=10)

        # Running status (hidden initially)
        self._status_frame = ctk.CTkFrame(self._body, fg_color="transparent")

        self._running_status_lbl = ctk.CTkLabel(
            self._status_frame,
            text=f"{tool_name} is running — merge your mods, then save and close.",
            font=FONT_NORMAL,
            text_color=ACCENT,
        )
        self._running_status_lbl.pack(pady=(10, 5))

        self._running_progress = ctk.CTkProgressBar(
            self._status_frame,
            width=300,
            height=8,
            mode="indeterminate",
            progress_color=ACCENT,
        )
        self._running_progress.pack()

    def _show_step(self):
        """Routes to the correct step UI based on state and updates navigation
        buttons."""
        if self._step == 0:
            self._show_step_0()
        elif self._step == 1:
            self._show_step_1()
        elif self._step == 2:
            self._show_step_2()

        # Update Back button state
        if self._step == 0:
            self._back_btn.configure(state="disabled")
        else:
            self._back_btn.configure(state="normal")

        # Update Next button text
        if self._step < 2:
            self._next_btn.configure(text="Next →")
            if self._step == 0:
                has_merge_tool = getattr(self._game, "has_merge_tool", False)
                state = "normal" if has_merge_tool else "disabled"
                self._next_btn.configure(state=state)
            else:
                self._next_btn.configure(state="normal")
        else:
            self._next_btn.configure(text="Finish")
            self._next_btn.configure(state="normal")

    def _detect_bin_conflicts(self) -> dict[str, list[str]]:
        """Scans enabled mods for definition file usage."""
        staging = self._game.get_effective_mod_staging_path()
        profile_dir = staging.parent

        modlist = read_modlist(profile_dir / "modlist.txt")
        enabled_mods = [e.name for e in modlist if e.enabled and not e.is_separator]

        results: dict[str, list[str]] = {}
        for mod_name in enabled_mods:
            mod_root = staging / mod_name
            if not mod_root.is_dir():
                continue

            # Scan mod folder for target bin names or associated .fmp files
            for bin_name in self._bin_names:
                base = Path(bin_name).stem
                found = False
                for ext in (".bin", ".fmp"):
                    target = (base + ext).lower()
                    # Recursively check for matches to handle varied mod structures
                    for p in mod_root.rglob("*"):
                        if p.is_file() and p.name.lower() == target:
                            results.setdefault(target, []).append(mod_name)
                            found = True
                            break
                    if found:
                        break
        return results

    def _render_conflict_list(self, conflicts: dict[str, list[str]]):
        """Renders the list of detected definition files and their providers."""
        scroll = ctk.CTkScrollableFrame(
            self._body, fg_color=BG_PANEL, corner_radius=6, height=250
        )
        scroll.pack(fill="both", expand=True, padx=20)

        for filename, mods in sorted(conflicts.items()):
            is_conflict = len(mods) > 1
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=4, padx=10)

            # File Name
            ctk.CTkLabel(
                row,
                text=filename,
                font=FONT_BOLD,
                text_color=ACCENT if is_conflict else TEXT_MAIN,
                width=120,
                anchor="w",
            ).pack(side="left")

            # Status/Mod List
            if is_conflict:
                status_text = f"CONFLICT: {', '.join(mods)}"
                status_color = "#e06c6c"  # Red
            else:
                status_text = mods[0]
                status_color = TEXT_DIM

            ctk.CTkLabel(
                row,
                text=status_text,
                font=FONT_SMALL,
                text_color=status_color,
                anchor="w",
            ).pack(side="left", fill="x", expand=True)

    def _do_launch(self):
        """Launches the selected tool under Proton and waits for exit."""
        tool_exe = getattr(self._game, "_merge_tool_path", None)
        if not tool_exe or not tool_exe.is_file():
            self._log("Merge Wizard: Executable not found.")
            return

        tool_type = getattr(self._game, "_merge_tool_type", "egocore")
        tool_name = "EgoCore" if tool_type == "egocore" else "Fable Explorer"

        proton_script, env = self._get_proton_env()
        if not proton_script or env is None:
            self._log(f"{tool_name} Merge: Could not find Proton or build environment.")
            return

        game_path = self._game.get_game_path()
        if not game_path:
            return

        sandbox_dir = self._game.get_profile_root() / "fable_working_dir"
        if not sandbox_dir.is_dir():
            self._log(
                "Merge Wizard: Sandbox not prepared. Please go back and prepare it."
            )
            return

        working_dir = sandbox_dir

        # Update UI to running state
        self._launch_btn.configure(state="disabled", text=f"{tool_name} Running...")
        self._status_frame.pack(pady=10)
        self._running_progress.start()
        self._back_btn.configure(state="disabled")
        self._next_btn.configure(state="disabled")

        def _wait():
            try:
                proc = subprocess.Popen(
                    ["python3", str(proton_script), "run", str(tool_exe)],
                    env=env,
                    cwd=str(working_dir),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.wait()
            except Exception as e:
                self._log(f"Merge Wizard: Process error: {e}")

            def _done():
                try:
                    self._running_progress.stop()
                    self._running_status_lbl.configure(
                        text=f"{tool_name} closed successfully.",
                        text_color="#6bc76b",
                    )
                    self._launch_btn.configure(
                        state="normal", text=f"Relaunch {tool_name}"
                    )
                    self._next_btn.configure(state="normal")
                    self._apply_merged_bins()
                except Exception:
                    pass

            self.after(0, _done)

        threading.Thread(target=_wait, daemon=True).start()

    def _apply_merged_bins(self):
        """Copies merged bins from sandbox back to the game directory."""
        sandbox_dir = self._game.get_profile_root() / "fable_working_dir"
        game_path = self._game.get_game_path()
        if not game_path:
            return
        compiled_defs = game_path / self._game_subpath

        applied = False
        for name in self._bin_names:
            if name.endswith(".bin"):
                src = sandbox_dir / name
                dst = compiled_defs / name
                if src.is_file():
                    # Overwrite the deployed file (which might be a link)
                    if dst.exists() or dst.is_symlink():
                        dst.unlink()
                    shutil.copy2(src, dst)
                    applied = True

        if applied:
            self._log("Merge Wizard: Merged BINs applied to game directory.")

    def _get_proton_env(self) -> tuple[Path | None, dict[str, str] | None]:
        """Builds the Proton environment for launching the external tool."""
        from Utils.steam_finder import (
            find_any_installed_proton,
            find_proton_for_game,
            find_steam_root_for_proton_script,
        )

        prefix_path = self._game.get_prefix_path()
        if prefix_path is None or not prefix_path.is_dir():
            return None, None

        steam_id = getattr(self._game, "steam_id", "")
        # Steam expects STEAM_COMPAT_DATA_PATH to be the dir containing 'pfx'
        compat_data = prefix_path.parent if prefix_path.name == "pfx" else prefix_path

        proton_script = find_proton_for_game(steam_id) if steam_id else None

        if proton_script is None:
            proton_script = find_any_installed_proton()
            if proton_script is None:
                return None, None

        steam_root = find_steam_root_for_proton_script(proton_script)
        if steam_root is None:
            return None, None

        env = os.environ.copy()
        env["STEAM_COMPAT_DATA_PATH"] = str(compat_data)
        env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(steam_root)
        game_path = self._game.get_game_path()
        if game_path:
            env["STEAM_COMPAT_INSTALL_PATH"] = str(game_path)
        if steam_id:
            env.setdefault("SteamAppId", steam_id)
            env.setdefault("SteamGameId", steam_id)

        return proton_script, env

    def _go_next(self):
        self._step = min(3, self._step + 1)
        if self._step > 2:
            self._on_cancel()
        else:
            self._show_step()

    def _go_back(self):
        self._step = max(0, self._step - 1)
        self._show_step()
