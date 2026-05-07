"""
fable_anniversary.py
Game handler for Fable Anniversary (UE3).

Routing and Structure:
  Uses dynamic suffix routing via vanilla game snapshot to map files into the
  vanilla tree with correct filesystem casing. Staged mods live in
  Profiles/Fable Anniversary/mods/.

  The game uses DirectX 9 and supports Steam Workshop.
  Mods are art/texture/model replacements (no gameplay mods).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from Games.base_game import BaseGame, WizardTool
from Utils.config_paths import get_profiles_dir
from Utils.deploy import (
    _FILEMAP_SNAPSHOT_NAME,
    CustomRule,
    LinkMode,
    _move_runtime_files,
    _resolve_nocase,
    _resolve_root_path,
    _write_deploy_snapshot,
    expand_separator_deploy_paths,
    expand_separator_raw_deploy,
    load_per_mod_strip_prefixes,
    load_separator_deploy_paths,
)
from Utils.modlist import read_modlist

_PROFILES_DIR = get_profiles_dir()

# Manifest written next to filemap.txt so restore knows exactly what to remove
_DEPLOYED_MANIFEST = "fable_deployed.txt"

# Vanilla files displaced by mod files are backed up here (inside the game root)
_VANILLA_BACKUP_DIR = "Amethyst_vanilla_files"


class FableAnniversary(BaseGame):
    """
    Handler for Fable Anniversary using a snapshot-based dynamic routing pipeline.
    Stops the core auto-stripper on any recognized vanilla directory and
    performs case-corrected deployment.
    """

    # -----------------------------------------------------------------------
    # Routing Constants
    # -----------------------------------------------------------------------
    _TOP_LEVEL_PREFIXES = (
        "Binaries/",
        "Engine/",
        "WellingtonGame/",
    )  # Bypass JSON routing

    def __init__(self):
        self._game_path: Path | None = None
        self._prefix_path: Path | None = None
        self._deploy_mode: LinkMode = LinkMode.HARDLINK
        self._staging_path: Path | None = None
        self._merge_tool_path: Path | None = None
        self._merge_tool_type: str = "egocore"
        self._log = lambda _: None
        self.__dict__["exe_name"] = "Binaries/Win32/Fable Anniversary.exe"
        self._vanilla_suffix_index: dict[str, str] | None = None
        self.load_paths()
        self._load_tool_config()

    # -----------------------------------------------------------------------
    # Identity
    # -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "Fable Anniversary"

    @property
    def game_id(self) -> str:
        return "fable_anniversary"

    def exe_name(self) -> str:
        return "Binaries/Win32/Fable Anniversary.exe"

    @property
    def steam_id(self) -> str:
        return "288470"

    @property
    def nexus_game_domain(self) -> str:
        return "fableanniversary"

    @property
    def reshade_dll(self) -> str:
        return "d3d9.dll"

    @property
    def reshade_arch(self) -> int:
        """Fable Anniversary is a 32-bit game."""
        return 32

    @property
    def default_deploy_mode(self) -> str:
        """Explicitly signal Hardlink as recommended for this game."""
        return "hardlink"

    @property
    def wine_dll_overrides(self) -> dict[str, str]:
        """Force Proton to load Windows ASI loaders/DLLs from the game folder."""
        # Required for d3d9.dll (ReShade) and dinput8.dll (ASI loaders) to
        # work on Linux.
        return {"dinput8.dll": "native,builtin", "d3d9.dll": "native,builtin"}

    # -----------------------------------------------------------------------
    # Mod Structure Configuration
    # -----------------------------------------------------------------------

    @property
    def mod_folder_strip_prefixes(self) -> set[str]:
        """Strips common redundant top-level folders from mod archives."""
        # We strip these so mods packed with WellingtonGame/ or Build/
        # drop their files correctly into the deep Data path.
        return {"wellingtongame", "build"}

    @property
    def conflict_ignore_filenames(self) -> set[str]:
        """Ignores meta/readme files that commonly cause false positives."""
        return {
            "readme.txt",
            "meta.ini",
            "license.txt",
            "after.jpg",
            "before.jpg",
            "install.txt",
        }

    @property
    def mod_required_top_level_folders(self) -> set[str]:
        """Vanilla directories that signal a valid mod structure, stopping the
        core's auto-stripper as soon as any valid game folder is encountered."""
        return {"wellingtongame", "engine", "binaries"}

    @property
    def mod_auto_strip_until_required(self) -> bool:
        """Allows the installer to automatically reach deep mod structures."""
        return True

    @property
    def mod_install_as_is_if_no_match(self) -> bool:
        """If a mod doesn't match required folders, install as-is."""
        return True

    @property
    def custom_routing_rules(self) -> list[CustomRule]:
        """
        Routes for non-vanilla file types.
        Standard game directory routing (Content, Binaries, etc.) is handled
        dynamically via vanilla suffix matching to ensure correct case-matching.
        """
        return [
            CustomRule(dest="TexMod", extensions=[".tpf"], flatten=True),
            # Cleanup rule for .backup files to prevent game dir pollution.
            # (Note: Separate from the vanilla file backup system in the deploy loop).
            CustomRule(
                dest="Backups",
                extensions=[".backup", ".bak", ".orig"],
            ),
        ]

    @property
    def wizard_tools(self) -> list[WizardTool]:
        """Adds Fable-specific tools to the wizard menu."""
        tools = self._base_wizard_tools()
        tools.append(
            WizardTool(
                id="fable_bin_merger",
                label="Merge .bin Mods (EgoCore/Fable Explorer)",
                description=(
                    "Launch a merge tool to resolve per-entry conflicts in "
                    "gameplay definition files (.bin/.fmp)."
                ),
                dialog_class_path="Plugins.fable_bin_merge.FableBinMergeWizard",
                extra={
                    "game_subpath": (
                        "WellingtonGame/FableData/Build/Data/CompiledDefs/Development"
                    ),
                    "bin_names": ["names.bin", "game.bin", "gamehard.bin"],
                },
            )
        )
        return tools

    # -----------------------------------------------------------------------
    # Game Discovery
    # -----------------------------------------------------------------------

    def find_game_path(self) -> Path | None:
        """
        Tiered search for Fable Anniversary install directory.
        Returns Path to game root or None.
        """

        def validate(p: Path) -> bool:
            return (p / self.exe_name()).is_file()

        def get_vdf_libraries(steam_root: Path) -> list[Path]:
            vdf = steam_root / "steamapps" / "libraryfolders.vdf"
            if not vdf.is_file():
                return []
            try:
                content = vdf.read_text(encoding="utf-8", errors="replace")
                # Simple regex to find "path" values in VDF
                paths = re.findall(r'"path"\s+"([^"]+)"', content)
                return [Path(p) for p in paths]
            except Exception:
                return []

        # Standard Native and Flatpak Steam roots
        roots = [
            Path.home() / ".local" / "share" / "Steam",
            Path.home() / ".steam" / "steam",
            Path.home()
            / ".var"
            / "app"
            / "com.valvesoftware.Steam"
            / ".local"
            / "share"
            / "Steam",
        ]

        for root in roots:
            if not root.is_dir():
                continue

            libraries = get_vdf_libraries(root)
            # Always include the root itself as a potential library
            if root not in libraries:
                libraries.append(root)

            for lib in libraries:
                candidate = lib / "steamapps" / "common" / "Fable Anniversary"
                if validate(candidate):
                    return candidate

        return None

    # -----------------------------------------------------------------------
    # Deployment
    # -----------------------------------------------------------------------

    def deploy(
        self,
        log_fn=None,
        mode: LinkMode = LinkMode.HARDLINK,
        profile: str = "default",
        progress_fn=None,
    ) -> None:
        """
        Deploy staged mods into the game directory with vanilla file backups.
        """
        _log = log_fn or (lambda _: None)
        self._log = _log

        game_path = self.get_game_path()
        if game_path is None:
            raise RuntimeError("Game path is not configured.")

        filemap = self.get_effective_filemap_path()
        staging = self.get_effective_mod_staging_path()

        if not filemap.is_file():
            raise RuntimeError(
                f"filemap.txt not found: {filemap}\n"
                "Run 'Build Filemap' before deploying."
            )

        profile_dir = self.get_profile_root() / "profiles" / profile
        per_mod_strip = load_per_mod_strip_prefixes(profile_dir)
        _sep_deploy = load_separator_deploy_paths(profile_dir)
        _sep_entries = read_modlist(profile_dir / "modlist.txt") if _sep_deploy else []
        per_mod_deploy = expand_separator_deploy_paths(_sep_deploy, _sep_entries)
        per_mod_raw = expand_separator_raw_deploy(_sep_deploy, _sep_entries)
        overwrite_dir = staging.parent / "overwrite"
        vanilla_backup_dir = game_path / _VANILLA_BACKUP_DIR

        # Load any existing manifest so we can distinguish previously-deployed
        # mod files (hardlinks that look like regular files) from real vanilla
        # files.
        manifest_path = self.get_profile_root() / _DEPLOYED_MANIFEST
        _already_deployed: set[str] = set()
        if manifest_path.is_file():
            try:
                _already_deployed = {
                    ln.strip().lower()
                    for ln in manifest_path.read_text(encoding="utf-8").splitlines()
                    if ln.strip()
                }
            except OSError:
                pass

        manifest: list[str] = []
        linked = 0
        skipped = 0
        backed_up = 0
        nocase_cache: dict[Path, dict[str, list[Path]]] = {}
        _dst_dir_cache: dict[Path, dict[str, str]] = {}
        _placed_this_run: set[str] = set()
        bin_conflicts: dict[str, list[str]] = {}

        lines = [
            ln.rstrip("\n")
            for ln in filemap.read_text(encoding="utf-8").splitlines()
            if "\t" in ln
        ]
        total = len(lines)

        for i, line in enumerate(lines):
            staged_rel, mod_name = line.split("\t", 1)
            staged_rel = staged_rel.replace("\\", "/")
            base_dir = per_mod_deploy.get(mod_name, game_path)

            if mod_name in per_mod_raw:
                final_rel = staged_rel
                dest_dir = base_dir
                dest_file = dest_dir / final_rel
            elif mod_name in per_mod_deploy:
                # Separator mods with explicit custom paths bypass game routing
                final_rel = staged_rel
                dest_dir = base_dir
                dest_file = dest_dir / final_rel
            else:
                dest_prefix, final_rel = self._route_path(staged_rel.replace("\\", "/"))
                dest_dir = (base_dir / dest_prefix) if dest_prefix else base_dir
                dest_file = dest_dir / final_rel

            src = self._find_staged_file(
                staging,
                mod_name,
                staged_rel,
                per_mod_strip.get(mod_name, []),
                overwrite_dir,
                nocase_cache,
            )
            if src is None:
                _log(f"  WARN: source not found for {staged_rel} ({mod_name})")
                skipped += 1
                continue

            try:
                # Handle casing resolution
                rel_from_game = dest_file.relative_to(game_path)
                actual_dest = _resolve_root_path(
                    game_path, rel_from_game, dir_cache=_dst_dir_cache
                )

                game_rel = actual_dest.relative_to(game_path)
                game_rel_lower = game_rel.as_posix().lower()

                # Track .bin/.fmp conflicts before the placement skip check
                if game_rel_lower.endswith((".bin", ".fmp")):
                    mods = bin_conflicts.setdefault(game_rel_lower, [])
                    if mod_name not in mods:
                        mods.append(mod_name)

                # Skip if this logical destination was already placed in this run
                # (e.g., two mods providing the same file with different casing).
                if game_rel_lower in _placed_this_run:
                    skipped += 1
                    continue

                actual_dest.parent.mkdir(parents=True, exist_ok=True)

                # Back up real vanilla files
                if actual_dest.is_file() and not actual_dest.is_symlink():
                    if game_rel_lower not in _already_deployed:
                        try:
                            _dest_stat = actual_dest.stat()
                            _src_stat = src.stat()
                            is_our_hardlink = (
                                _dest_stat.st_ino == _src_stat.st_ino
                                and _dest_stat.st_dev == _src_stat.st_dev
                            )
                        except OSError:
                            is_our_hardlink = False

                        if not is_our_hardlink:
                            backup_target = vanilla_backup_dir / game_rel
                            if not backup_target.exists():
                                backup_target.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(actual_dest, backup_target)
                                backed_up += 1

                if actual_dest.exists() or actual_dest.is_symlink():
                    actual_dest.unlink()

                if mode == LinkMode.SYMLINK:
                    actual_dest.symlink_to(src)
                elif mode == LinkMode.COPY:
                    shutil.copy2(src, actual_dest)
                else:
                    try:
                        actual_dest.hardlink_to(src)
                    except (OSError, NotImplementedError):
                        shutil.copy2(src, actual_dest)

                manifest.append(game_rel.as_posix())
                _placed_this_run.add(game_rel_lower)
                linked += 1
            except OSError as exc:
                _log(f"  ERROR placing {final_rel}: {exc}")
                skipped += 1

            if progress_fn:
                progress_fn(i + 1, total)

        # Write manifest
        manifest_path = self.get_profile_root() / _DEPLOYED_MANIFEST
        # Write manifest atomically to prevent corruption if app crashes mid-write
        temp_manifest = manifest_path.with_suffix(".txt.tmp")
        try:
            temp_manifest.write_text("\n".join(manifest), encoding="utf-8")
            os.replace(temp_manifest, manifest_path)
        except OSError:
            # Clean up the temp file if the swap failed, leave old manifest intact
            temp_manifest.unlink(missing_ok=True)

        # Snapshot game root so restore can identify runtime-generated files.
        # Even though UE3 usually uses Documents, ASI injectors or newer tools
        # often create logs or caches in the Binaries folder.
        snapshot_path = self.get_profile_root() / _FILEMAP_SNAPSHOT_NAME
        try:
            _write_deploy_snapshot(game_path, snapshot_path, log_fn=_log)
        except Exception as exc:
            _log(f"  WARN: could not write deploy snapshot: {exc}")

        # Log bin/fmp conflict summary
        conflicts = {p: mods for p, mods in bin_conflicts.items() if len(mods) > 1}
        if conflicts:
            n = len(conflicts)
            if self.has_merge_tool:
                _log(
                    f"NOTE: {n} .bin conflict(s) detected. "
                    "Use 'Merge .bin Mods' wizard for per-entry merging."
                )
            else:
                _log(
                    f"WARNING: {n} .bin conflict(s) detected. "
                    "Without a merge tool, the highest-priority mod's "
                    "file wins entirely. Configure a merge tool for "
                    "per-entry merging."
                )

            for rel_path, mods in conflicts.items():
                filename = Path(rel_path).name
                _log(f"  {filename}: {', '.join(mods)}")

        backed_msg = f", {backed_up} vanilla file(s) backed up" if backed_up else ""
        _log(f"Deploy complete. {linked} file(s) placed{backed_msg}.")

    def restore(self, log_fn=None, progress_fn=None) -> None:
        """Remove deployed mods and restore vanilla backups."""
        _log = log_fn or (lambda _: None)
        self._log = _log
        game_path = self.get_game_path()
        if game_path is None:
            _log("Restore: Game path is not configured.")
            return

        manifest_path = self.get_profile_root() / _DEPLOYED_MANIFEST

        if manifest_path.is_file():
            lines = manifest_path.read_text(encoding="utf-8").splitlines()
            removed = 0
            dirs_to_check: set[Path] = set()

            for rel in lines:
                target = game_path / rel
                if target.is_file() or target.is_symlink():
                    target.unlink()
                    removed += 1
                    p = target.parent
                    while p != game_path:
                        dirs_to_check.add(p)
                        p = p.parent

            # RESTORE VANILLA FILES
            vanilla_backup_dir = game_path / _VANILLA_BACKUP_DIR
            restored_vanilla = 0
            restore_failed = False
            if vanilla_backup_dir.is_dir():
                for backup_file in vanilla_backup_dir.rglob("*"):
                    if not backup_file.is_file():
                        continue
                    rel = backup_file.relative_to(vanilla_backup_dir)
                    dest = game_path / rel
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(backup_file), dest)
                        restored_vanilla += 1
                    except OSError as exc:
                        _log(f"  ERROR: Failed to restore {rel}: {exc}")
                        restore_failed = True

                if not restore_failed:
                    try:
                        shutil.rmtree(vanilla_backup_dir)
                    except OSError:
                        pass
                else:
                    _log("  WARNING: Restore incomplete. Backup directory preserved.")

            # Prune empty dirs
            for d in sorted(dirs_to_check, key=lambda p: len(p.parts), reverse=True):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()

            manifest_path.unlink(missing_ok=True)
            _log(f"Restore complete. {restored_vanilla} vanilla file(s) restored.")

        # Move runtime-generated files (logs, dynamic configs) to overwrite/
        snapshot_path = self.get_profile_root() / _FILEMAP_SNAPSHOT_NAME
        if snapshot_path.is_file():
            overwrite_dir = self.get_effective_mod_staging_path().parent / "overwrite"
            _log("  Scanning game root for runtime-generated files ...")
            moved = _move_runtime_files(
                game_path, snapshot_path, overwrite_dir, log_fn=_log
            )
            if moved:
                _log(f"  Moved {moved} runtime-generated file(s) to overwrite/.")
            try:
                snapshot_path.unlink()
            except OSError:
                pass

    # -----------------------------------------------------------------------
    # Merge Tool Configuration
    # -----------------------------------------------------------------------

    @property
    def has_merge_tool(self) -> bool:
        """Returns True if the merge tool path is configured and valid."""
        return self._merge_tool_path is not None and self._merge_tool_path.is_file()

    def set_merge_tool(
        self, path: Path | str | None, tool_type: str = "egocore"
    ) -> None:
        """Validates and sets the path to the merge tool executable."""
        if path:
            p = Path(path)
            self._merge_tool_path = p if p.is_file() else None
        else:
            self._merge_tool_path = None
        self._merge_tool_type = tool_type
        self._save_tool_config()

    def _load_tool_config(self) -> None:
        """Load tool settings from a separate config file."""
        config_path = self._paths_file.parent / "fable_tool_config.json"
        if config_path.is_file():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                raw_path = data.get("merge_tool_path")
                self._merge_tool_path = Path(raw_path) if raw_path else None
                self._merge_tool_type = data.get("merge_tool_type", "egocore")
            except (OSError, json.JSONDecodeError):
                pass

    def _save_tool_config(self) -> None:
        """Save tool settings to a separate config file."""
        config_path = self._paths_file.parent / "fable_tool_config.json"
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "merge_tool_path": str(self._merge_tool_path)
                if self._merge_tool_path
                else "",
                "merge_tool_type": self._merge_tool_type,
            }
            config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _get_vanilla_index(self) -> dict[str, str]:
        if self._vanilla_suffix_index is not None:
            return self._vanilla_suffix_index

        game_path = self.get_game_path()
        self._vanilla_suffix_index = {}

        if game_path and game_path.is_dir():
            for f in game_path.rglob("*"):
                if not f.is_file():
                    continue
                rel = f.relative_to(game_path).as_posix()
                parts = rel.split("/")
                for i in range(len(parts)):
                    suffix = "/".join(parts[i:]).lower()
                    self._vanilla_suffix_index[suffix] = rel
        return self._vanilla_suffix_index

    def _route_path(self, staged_rel: str) -> tuple[str, str]:
        """
        Dynamic Routing Strategy: Routes files to correct directories
        based on the game's vanilla directory structure.
        """
        norm = staged_rel.replace("\\", "/")

        # Tier 1 (Pass-through): Keep existing logic for _TOP_LEVEL_PREFIXES
        if norm.startswith(self._TOP_LEVEL_PREFIXES):
            return "", staged_rel

        index = self._get_vanilla_index()
        parts = norm.lower().split("/")

        # Build suffixes (longest first)
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            if suffix in index:
                vanilla_rel = index[suffix]
                # Split matched path into dest_dir (parent) and filename (basename)
                dest_dir = os.path.dirname(vanilla_rel)
                if dest_dir and not dest_dir.endswith("/"):
                    dest_dir += "/"
                filename = os.path.basename(vanilla_rel)
                return dest_dir, filename

        # Tier 4 (Unrouted Fallback): If no routes match, log and return as-is
        self._log(f"  DEBUG: Unrouted file '{staged_rel}' - falling back to root.")
        return "", staged_rel

    def post_build_filemap(self, filemap_path: Path, staging_path: Path) -> None:
        """
        Rewrite the final routed paths in filemap.txt instead of raw
        staged paths, so the UI displays the correct destination paths.
        """
        filemap = filemap_path
        if not filemap or not filemap.is_file():
            return

        lines = filemap.read_text(encoding="utf-8").splitlines()
        routed_lines = []
        for line in lines:
            if "\t" not in line:
                routed_lines.append(line)
                continue
            staged_rel, mod_name = line.split("\t", 1)
            dest_prefix, final_rel = self._route_path(staged_rel.replace("\\", "/"))
            final_path = f"{dest_prefix}{final_rel}" if dest_prefix else final_rel
            routed_lines.append(f"{final_path}\t{mod_name}")

        filemap.write_text("\n".join(routed_lines), encoding="utf-8")

    # -----------------------------------------------------------------------
    # Paths
    # -----------------------------------------------------------------------

    def get_game_path(self) -> Path | None:
        return self._game_path

    def get_mod_data_path(self) -> Path | None:
        """Base deployment path for standard mods is the deep Data folder."""
        if self._game_path is None:
            return None
        return self._game_path / "WellingtonGame" / "FableData" / "Build" / "Data"

    def get_user_profile_path(self) -> Path:
        """
        Returns the path to the Fable Anniversary configuration directory.

        Note: UE3 Configs are in 'UnrealEngine3/WellingtonGame/Config'.
        Save games are typically located in 'Documents/My Games/FableHD/Saves'.
        """
        documents = Path.home() / "Documents"
        return documents / "My Games" / "UnrealEngine3" / "WellingtonGame" / "Config"

    def get_mod_staging_path(self) -> Path:
        if self._staging_path is not None:
            return self._staging_path / "mods"
        return _PROFILES_DIR / self.name / "mods"

    def get_prefix_path(self) -> Path | None:
        return self._prefix_path

    def get_deploy_mode(self) -> LinkMode:
        return self._deploy_mode

    def set_deploy_mode(self, mode: LinkMode) -> None:
        self._deploy_mode = mode
        self.save_paths()

    def set_prefix_path(self, path: Path | str | None) -> None:
        self._prefix_path = Path(path) if path else None
        self.save_paths()

    def set_staging_path(self, path: Path | str | None) -> None:
        self._staging_path = Path(path) if path else None
        self.save_paths()

    def _find_staged_file(
        self,
        staging: Path,
        mod_name: str,
        staged_rel: str,
        mod_strips: list[str],
        overwrite_dir: Path,
        cache: dict | None = None,
    ) -> Path | None:
        ow = overwrite_dir / staged_rel
        if ow.is_file():
            return ow

        mod_root = staging / mod_name
        norm = staged_rel.replace("\\", "/")

        # Direct match (handles routed paths that actually exist in zip)
        src = _resolve_nocase(mod_root, norm, cache=cache)
        if src:
            return src

        # Suffix finder logic: Find a file in mod_root whose relative path
        # is a suffix of the routed destination path (norm.lower()).
        best_match: Path | None = None
        best_match_len = -1

        for root_dir, _, files in os.walk(mod_root):
            for file_name in files:
                full_p = Path(root_dir) / file_name
                rel_p = full_p.relative_to(mod_root).as_posix().lower()

                if norm.lower().endswith(rel_p):
                    if len(rel_p) > best_match_len:
                        best_match = full_p
                        best_match_len = len(rel_p)

        if best_match:
            return best_match

        # Global strips (fuzzy matching)
        for prefix in sorted(self.mod_folder_strip_prefixes, key=len, reverse=True):
            src = _resolve_nocase(mod_root, prefix + "/" + norm, cache=cache)
            if src:
                return src

        # Per-mod strips (user overrides)
        for prefix in sorted(mod_strips, key=len, reverse=True):
            src = _resolve_nocase(mod_root, prefix + "/" + norm, cache=cache)
            if src:
                return src
        return None
