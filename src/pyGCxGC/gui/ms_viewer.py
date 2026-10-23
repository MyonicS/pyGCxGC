"""
GCxGC-MS viewer: 2D chromatogram, mass spectrum and library match.

Layout: the 2D chromatogram fills the left two thirds of the window; the
top right shows the mass spectrum at the selected point and the bottom right
a head-to-tail comparison with the selected library hit plus the hit list.

Launch with ``pygcxgc-msviewer [data.cdf] [--library MAINLIB|file.msp]`` or
``pyGCxGC.launch_ms_viewer()``.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.colors import LogNorm, PowerNorm
from matplotlib.figure import Figure

from pyGCxGC.ms.data import GCxGC_MS, estimate_modulation_time
from pyGCxGC.ms.io import read_ms_file
from pyGCxGC.ms.library import download_mona_gcms, load_library
from pyGCxGC.ms.peaks import detect_peaks, identify_peaks
from pyGCxGC.ms.search import SearchParameters, search_spectrum

SETTINGS_FILE = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "pyGCxGC"
    / "ms_viewer.json"
)
HIT_COLUMNS = ("rank", "match", "reverse_match", "name", "formula", "mw", "cas")
PEAK_COLUMNS = (
    "peak_id",
    "rt1_min",
    "rt2_s",
    "height",
    "match",
    "reverse_match",
    "name",
)


class MSViewer:
    """Tkinter application for browsing GCxGC-MS data and matching spectra."""

    def __init__(self, root: tk.Tk, data_path=None, library_path=None, modulation=None):
        self.root = root
        self.root.title("pyGCxGC - GCxGC-MS viewer")
        self.root.geometry("1500x900")
        self.data: GCxGC_MS | None = None
        self.library = None
        self.spectrum = None
        self.hits = []
        self.marker = None
        self.peaks = None
        self.peak_artist = None
        self.jobs: queue.Queue = queue.Queue()
        self.settings = self._load_settings()

        self.var_mod = tk.StringVar(
            value=str(modulation or self.settings.get("modulation", ""))
        )
        self.var_shift = tk.StringVar(value="0")
        self.var_scale = tk.StringVar(value="sqrt")
        self.var_eic = tk.StringVar(value="")
        self.var_avg = tk.IntVar(value=3)
        self.var_snap = tk.BooleanVar(value=True)
        self.var_snr = tk.StringVar(value="10")
        self.var_show_peaks = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(
            value="Open a GCxGC-MS file (ANDI .cdf) and a library."
        )
        self._build_ui()
        self.root.after(100, self._poll_jobs)

        data_path = data_path or self.settings.get("data")
        library_path = library_path or self.settings.get("library")
        if library_path and Path(library_path).exists():
            self.load_library(library_path)
        if data_path and Path(data_path).exists():
            self.load_data(data_path)

    # ---------------------------------------------------------------- layout
    def _build_ui(self):
        bar = ttk.Frame(self.root, padding=4)
        bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(bar, text="Open data…", command=self.ask_data).pack(side=tk.LEFT)
        ttk.Button(bar, text="Load library…", command=self.ask_library).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Label(bar, text="Modulation (s)").pack(side=tk.LEFT, padx=(12, 2))
        ttk.Entry(bar, textvariable=self.var_mod, width=6).pack(side=tk.LEFT)
        ttk.Label(bar, text="Shift (scans)").pack(side=tk.LEFT, padx=(8, 2))
        ttk.Entry(bar, textvariable=self.var_shift, width=5).pack(side=tk.LEFT)
        ttk.Button(bar, text="Apply", command=self.refold).pack(side=tk.LEFT, padx=4)
        ttk.Label(bar, text="Scale").pack(side=tk.LEFT, padx=(12, 2))
        scale = ttk.Combobox(
            bar,
            textvariable=self.var_scale,
            values=("linear", "sqrt", "log"),
            width=6,
            state="readonly",
        )
        scale.pack(side=tk.LEFT)
        scale.bind("<<ComboboxSelected>>", lambda _e: self.draw_chromatogram())
        ttk.Label(bar, text="EIC m/z").pack(side=tk.LEFT, padx=(12, 2))
        eic = ttk.Entry(bar, textvariable=self.var_eic, width=10)
        eic.pack(side=tk.LEFT)
        eic.bind("<Return>", lambda _e: self.draw_chromatogram())
        ttk.Label(bar, text="Avg scans").pack(side=tk.LEFT, padx=(12, 2))
        ttk.Spinbox(
            bar, from_=1, to=15, increment=2, textvariable=self.var_avg, width=4
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(bar, text="Snap to apex", variable=self.var_snap).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Label(bar, text="Peak S/N").pack(side=tk.LEFT, padx=(0, 2))
        ttk.Entry(bar, textvariable=self.var_snr, width=5).pack(side=tk.LEFT)
        ttk.Button(bar, text="Detect & identify peaks", command=self.find_peaks).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Checkbutton(
            bar,
            text="Show peaks",
            variable=self.var_show_peaks,
            command=self.draw_peak_markers,
        ).pack(side=tk.LEFT)
        ttk.Button(bar, text="Export table…", command=self.export_peaks).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Label(
            self.root, textvariable=self.var_status, anchor=tk.W, relief=tk.SUNKEN
        ).pack(side=tk.BOTTOM, fill=tk.X)

        main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)
        self.main_pane = main
        left = ttk.Frame(main)
        right = ttk.PanedWindow(main, orient=tk.VERTICAL)
        main.add(left, weight=2)
        main.add(right, weight=1)

        self.fig_chrom = Figure(figsize=(9, 7), dpi=100, layout="constrained")
        self.ax_chrom = self.fig_chrom.add_subplot(111)
        self.canvas_chrom = FigureCanvasTkAgg(self.fig_chrom, master=left)
        self.toolbar = NavigationToolbar2Tk(self.canvas_chrom, left, pack_toolbar=False)
        self.toolbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas_chrom.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.canvas_chrom.mpl_connect("button_press_event", self.on_click)
        self.colorbar = None

        top = ttk.Frame(right)
        bottom = ttk.Frame(right)
        right.add(top, weight=1)
        right.add(bottom, weight=1)
        self.fig_spec = Figure(figsize=(5, 3.5), dpi=100, layout="constrained")
        self.ax_spec = self.fig_spec.add_subplot(111)
        self.canvas_spec = FigureCanvasTkAgg(self.fig_spec, master=top)
        self.canvas_spec.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.fig_match = Figure(figsize=(5, 3), dpi=100, layout="constrained")
        self.ax_match = self.fig_match.add_subplot(111)
        self.canvas_match = FigureCanvasTkAgg(self.fig_match, master=bottom)
        self.canvas_match.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.tabs = ttk.Notebook(bottom)
        self.tabs.pack(side=tk.BOTTOM, fill=tk.X)
        hit_frame, peak_frame = ttk.Frame(self.tabs), ttk.Frame(self.tabs)
        self.tabs.add(hit_frame, text="Library hits")
        self.tabs.add(peak_frame, text="Peak table")
        self.tree = ttk.Treeview(
            hit_frame, columns=HIT_COLUMNS, show="headings", height=8
        )
        widths = {
            "rank": 35,
            "match": 50,
            "reverse_match": 50,
            "name": 260,
            "formula": 90,
            "mw": 45,
            "cas": 80,
        }
        headings = {"reverse_match": "R.match"}
        for col in HIT_COLUMNS:
            self.tree.heading(col, text=headings.get(col, col.capitalize()))
            self.tree.column(col, width=widths[col], stretch=col == "name", anchor=tk.W)
        self.tree.pack(fill=tk.X)
        self.tree.bind("<<TreeviewSelect>>", self.on_hit_selected)
        self.peak_tree = ttk.Treeview(
            peak_frame, columns=PEAK_COLUMNS, show="headings", height=8
        )
        widths = {
            "peak_id": 40,
            "rt1_min": 60,
            "rt2_s": 50,
            "height": 70,
            "match": 50,
            "reverse_match": 50,
            "name": 240,
        }
        headings = {
            "peak_id": "#",
            "rt1_min": "RT1 (min)",
            "rt2_s": "RT2 (s)",
            "reverse_match": "R.match",
        }
        for col in PEAK_COLUMNS:
            self.peak_tree.heading(
                col,
                text=headings.get(col, col.capitalize()),
                command=lambda c=col: self._sort_peaks(c),
            )
            self.peak_tree.column(
                col, width=widths[col], stretch=col == "name", anchor=tk.W
            )
        scroll = ttk.Scrollbar(
            peak_frame, orient=tk.VERTICAL, command=self.peak_tree.yview
        )
        self.peak_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.peak_tree.pack(fill=tk.X)
        self.peak_tree.bind("<<TreeviewSelect>>", self.on_peak_selected)
        self._clear_plots()
        self.root.after(200, self._place_sash)

    def _place_sash(self):
        """Give the chromatogram two thirds of the window width."""
        self.root.update_idletasks()
        width = self.main_pane.winfo_width()
        if width > 100:
            self.main_pane.sashpos(0, int(width * 2 / 3))

    def _clear_plots(self):
        for ax, title in (
            (self.ax_chrom, "No data loaded"),
            (self.ax_spec, "Click a point in the chromatogram"),
            (self.ax_match, "Library match"),
        ):
            ax.clear()
            ax.set_title(title, fontsize=10)
        for c in (self.canvas_chrom, self.canvas_spec, self.canvas_match):
            c.draw_idle()

    # ---------------------------------------------------------------- jobs
    def _run(self, label, func, done):
        """Run ``func`` in a worker thread and call ``done(result)`` in Tk."""
        self.var_status.set(label + " …")

        def worker():
            try:
                self.jobs.put((done, func(), None))
            except Exception as exc:
                self.jobs.put((done, None, (exc, traceback.format_exc())))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_jobs(self):
        try:
            while True:
                done, result, error = self.jobs.get_nowait()
                if error:
                    self.var_status.set(f"Error: {error[0]}")
                    messagebox.showerror("pyGCxGC", f"{error[0]}\n\n{error[1][-1500:]}")
                else:
                    done(result)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_jobs)

    # ---------------------------------------------------------------- data
    def ask_data(self):
        path = filedialog.askopenfilename(
            title="Open GCxGC-MS data",
            filetypes=[
                ("ANDI/AIA netCDF", "*.cdf *.CDF"),
                ("OpenChrom CSV", "*.csv"),
                ("JCAMP-DX", "*.jdx *.JDX"),
                ("All files", "*"),
            ],
        )
        if path:
            self.load_data(path)

    def load_data(self, path):
        path = str(Path(path).resolve())

        def work():
            raw = read_ms_file(path)
            try:
                mod = float(self.var_mod.get())
            except ValueError:
                mod = estimate_modulation_time(raw)
            return GCxGC_MS(raw, mod, shift=self._shift(), name=Path(path).name)

        def done(data):
            self.data = data
            self.var_mod.set(f"{data.modulation_time:g}")
            self.settings.update(data=path, modulation=data.modulation_time)
            self._save_settings()
            self.draw_chromatogram()
            lo, hi = data.mass_range
            self.var_status.set(
                f"{data.name}: {data.n_scans} scans, m/z {lo}-{hi}, {data.n_modulations} modulations of "
                f"{data.modulation_time:g} s. Click a peak to see its spectrum."
            )

        self._run(f"Loading {Path(path).name}", work, done)

    def _shift(self) -> int:
        try:
            return int(self.var_shift.get())
        except ValueError:
            return 0

    def refold(self):
        if self.data is None:
            return
        try:
            self.data.set_modulation(float(self.var_mod.get()), self._shift())
        except ValueError as exc:
            messagebox.showerror("pyGCxGC", str(exc))
            return
        self.settings["modulation"] = self.data.modulation_time
        self._save_settings()
        self.draw_chromatogram()

    def draw_chromatogram(self):
        if self.data is None:
            return
        d = self.data
        eic = [
            int(x)
            for x in self.var_eic.get().replace(",", " ").split()
            if x.strip().isdigit()
        ]
        img = d.eic_2d(eic) if eic else d.tic_2d
        img = np.clip(img, 0, None)
        positive = img[img > 0]
        vmax = float(np.percentile(positive, 99.9)) if positive.size else 1.0
        scale = self.var_scale.get()
        if scale == "log":
            vmin = float(np.percentile(positive, 50)) if positive.size else 1.0
            norm = LogNorm(vmin=max(vmin, 1.0), vmax=max(vmax, vmin * 10, 2.0))
            img = np.maximum(img, max(vmin, 1.0))
        elif scale == "sqrt":
            norm = PowerNorm(0.5, vmin=0, vmax=vmax)
        else:
            norm = None
        self.ax_chrom.clear()
        im = self.ax_chrom.imshow(
            img,
            origin="lower",
            aspect="auto",
            cmap="viridis",
            norm=norm,
            vmax=None if norm else vmax,
            extent=d.limits,
            interpolation="nearest",
        )
        if self.colorbar is None:
            self.colorbar = self.fig_chrom.colorbar(im, ax=self.ax_chrom, pad=0.01)
        else:
            self.colorbar.update_normal(im)
        self.ax_chrom.set_xlabel("First dimension retention time (min)")
        self.ax_chrom.set_ylabel("Second dimension retention time (s)")
        self.ax_chrom.set_title(
            f"{d.name} - {'EIC m/z ' + ', '.join(map(str, eic)) if eic else 'TIC'}",
            fontsize=10,
        )
        self.marker = None
        self.peak_artist = None
        self.draw_peak_markers()

    # ---------------------------------------------------------------- peaks
    def find_peaks(self):
        if self.data is None:
            messagebox.showinfo("pyGCxGC", "Open a data file first.")
            return
        try:
            snr = float(self.var_snr.get())
        except ValueError:
            snr = 10.0
        data, lib = self.data, self.library
        n_avg = max(int(self.var_avg.get()), 1)

        def work():
            peaks = detect_peaks(data, snr=snr)
            if lib is not None and len(peaks):
                peaks, _ = identify_peaks(data, peaks, lib, n_average=n_avg)
            return peaks

        def done(peaks):
            self.peaks = peaks
            self._fill_peak_table()
            self.draw_peak_markers()
            self.tabs.select(1)
            ided = "" if lib is None else " and identified"
            self.var_status.set(f"{len(peaks)} peaks found{ided} (S/N >= {snr:g}).")

        what = "Detecting peaks" + ("" if lib is None else " and searching the library")
        self._run(what, work, done)

    def _fill_peak_table(self):
        self.peak_tree.delete(*self.peak_tree.get_children())
        if self.peaks is None:
            return
        for idx, row in self.peaks.iterrows():
            values = []
            for col in PEAK_COLUMNS:
                v = row.get(col, "")
                if col == "rt1_min" or col == "rt2_s":
                    v = f"{v:.2f}"
                elif col == "height":
                    v = f"{v:.3g}"
                values.append(v)
            self.peak_tree.insert("", tk.END, iid=str(idx), values=values)

    def _sort_peaks(self, column):
        if self.peaks is None or column not in self.peaks:
            return
        ascending = column in ("peak_id", "rt1_min", "rt2_s", "name")
        if getattr(self, "_last_sort", None) == column:
            ascending = not ascending
            self._last_sort = None
        else:
            self._last_sort = column
        self.peaks = self.peaks.sort_values(column, ascending=ascending)
        self._fill_peak_table()

    def on_peak_selected(self, _event=None):
        sel = self.peak_tree.selection()
        if sel and self.peaks is not None:
            self.select_scan(int(self.peaks.loc[int(sel[0]), "scan"]))

    def draw_peak_markers(self):
        if self.peak_artist is not None:
            self.peak_artist.remove()
            self.peak_artist = None
        if (
            self.data is not None
            and self.peaks is not None
            and len(self.peaks)
            and self.var_show_peaks.get()
        ):
            x = self.peaks["rt1_min"].to_numpy() + self.data.modulation_time / 120.0
            self.peak_artist = self.ax_chrom.scatter(
                x,
                self.peaks["rt2_s"],
                s=28,
                facecolors="none",
                edgecolors="white",
                linewidths=0.8,
            )
        self.canvas_chrom.draw_idle()

    def export_peaks(self):
        if self.peaks is None:
            messagebox.showinfo("pyGCxGC", "Detect peaks first.")
            return
        default = Path(self.data.name).stem + "_peaks.csv" if self.data else "peaks.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default, filetypes=[("CSV", "*.csv")]
        )
        if path:
            self.peaks.sort_values("peak_id").to_csv(path, index=False)
            self.var_status.set(f"Peak table written to {path}")

    # ---------------------------------------------------------------- library
    def ask_library(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Load spectral library")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame, text="Libraries are converted once and cached in ~/.cache/pyGCxGC."
        ).pack(anchor=tk.W, pady=(0, 8))

        def choose(kind):
            dialog.destroy()
            if kind == "nist":
                path = filedialog.askdirectory(
                    title="NIST MAINLIB (or MSSEARCH) folder"
                )
            elif kind == "file":
                path = filedialog.askopenfilename(
                    title="Spectral library",
                    filetypes=[
                        ("MSP / zip / cache", "*.msp *.MSP *.zip *.npz"),
                        ("All files", "*"),
                    ],
                )
            else:
                self._run(
                    "Downloading MoNA GC-MS library",
                    download_mona_gcms,
                    self.load_library,
                )
                return
            if path:
                self.load_library(path)

        for text, kind in (
            ("NIST MS Search MAINLIB folder…", "nist"),
            ("MSP / zip / npz file…", "file"),
            ("Download open MoNA GC-MS library (~9 MB)", "mona"),
        ):
            ttk.Button(frame, text=text, command=lambda k=kind: choose(k)).pack(
                fill=tk.X, pady=2
            )

    def load_library(self, path):
        path = str(Path(path).resolve())

        def done(lib):
            self.library = lib
            self.settings["library"] = path
            self._save_settings()
            self.var_status.set(f"Library {lib.name}: {len(lib):,} spectra loaded.")
            if self.spectrum is not None:
                self.search_current()
            if self.peaks is not None and "name" not in self.peaks:
                self.find_peaks()

        self._run(
            f"Loading library {Path(path).name} (first time conversion can take ~10 s)",
            lambda: load_library(path),
            done,
        )

    # ---------------------------------------------------------------- selection
    def on_click(self, event):
        if self.data is None or event.inaxes is not self.ax_chrom or event.button != 1:
            return
        if self.toolbar.mode:  # zoom / pan active
            return
        d = self.data
        j, i = d.pixel_at(event.xdata, event.ydata)
        scan = d.scan_at(j, i)
        if scan < 0:
            return
        if self.var_snap.get():
            scan = d.find_apex(scan)
        self.select_scan(scan)

    def select_scan(self, scan: int):
        d = self.data
        self.spectrum = d.spectrum(scan, n_average=max(int(self.var_avg.get()), 1))
        rt1, rt2 = self.spectrum.rt1, self.spectrum.rt2
        if self.marker is not None:
            self.marker.remove()
        (self.marker,) = self.ax_chrom.plot(
            [rt1 + d.modulation_time / 120.0],
            [rt2],
            marker="+",
            color="red",
            markersize=16,
            mew=2,
        )
        self.canvas_chrom.draw_idle()
        self.draw_spectrum()
        self.search_current()

    def draw_spectrum(self):
        s = self.spectrum.nonzero()
        ax = self.ax_spec
        ax.clear()
        if len(s.mz):
            rel = s.intensity / s.intensity.max() * 100
            ax.vlines(s.mz, 0, rel, color="C0", lw=1.2)
            for k in np.argsort(-rel)[:8]:
                ax.annotate(
                    str(int(s.mz[k])),
                    (s.mz[k], rel[k]),
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )
            ax.set_ylim(0, 112)
            ax.set_xlim(*_mz_limits(s.mz, rel))
        ax.set_xlabel("m/z")
        ax.set_ylabel("Relative intensity (%)")
        ax.set_title(
            f"RT1 {s.rt1:.2f} min, RT2 {s.rt2:.2f} s (scan {s.scan})", fontsize=10
        )
        self.canvas_spec.draw_idle()

    def search_current(self):
        if self.library is None or self.spectrum is None:
            if self.library is None:
                self.var_status.set("Load a spectral library to match spectra.")
            return
        spec, lib = self.spectrum, self.library
        lo, hi = self.data.mass_range
        params = SearchParameters(mz_min=lo, mz_max=hi, n_hits=20)

        def done(hits):
            if spec is not self.spectrum:
                return  # a newer point was selected meanwhile
            self.hits = hits
            self.tree.delete(*self.tree.get_children())
            for h in hits:
                d = h.as_dict()
                self.tree.insert(
                    "", tk.END, iid=str(h.rank), values=[d[c] for c in HIT_COLUMNS]
                )
            if hits:
                self.tree.selection_set("1")
                self.draw_match(hits[0])
                self.var_status.set(
                    f"Best match: {hits[0].name} (match {hits[0].match:.0f}, reverse {hits[0].reverse_match:.0f})"
                )
            else:
                self.var_status.set("No library hits (empty spectrum?)")
                self.draw_match(None)

        self._run(
            "Searching library",
            lambda: search_spectrum(spec.mz, spec.intensity, lib, params),
            done,
        )

    def on_hit_selected(self, _event=None):
        sel = self.tree.selection()
        if sel:
            self.draw_match(self.hits[int(sel[0]) - 1])

    def draw_match(self, hit):
        ax = self.ax_match
        ax.clear()
        s = self.spectrum.nonzero() if self.spectrum is not None else None
        shown_mz, shown_rel = [np.zeros(0)], [np.zeros(0)]
        if s is not None and len(s.mz):
            shown_mz.append(s.mz)
            shown_rel.append(s.intensity / s.intensity.max() * 100)
            ax.vlines(
                s.mz,
                0,
                s.intensity / s.intensity.max() * 100,
                color="C0",
                lw=1.2,
                label="Unknown",
            )
        if hit is not None:
            e = self.library.entry(hit.index)
            lo, hi = self.data.mass_range
            keep = (e.mz >= lo) & (e.mz <= hi)
            mz, it = e.mz[keep], e.intensity[keep]
            if len(it):
                rel = it / it.max() * 100
                shown_mz.append(mz)
                shown_rel.append(rel)
                ax.vlines(mz, 0, -rel, color="C3", lw=1.2, label="Library")
                for k in np.argsort(-rel)[:6]:
                    ax.annotate(
                        str(int(mz[k])),
                        (mz[k], -rel[k]),
                        ha="center",
                        va="top",
                        fontsize=8,
                    )
            ax.set_title(
                f"{hit.name[:70]}\nMF {hit.match:.0f}  RMF {hit.reverse_match:.0f}  {hit.formula}  MW {hit.mw}  CAS {hit.cas}",
                fontsize=9,
            )
        ax.axhline(0, color="k", lw=0.8)
        ax.set_ylim(-112, 112)
        all_mz = np.concatenate(shown_mz)
        if len(all_mz):
            ax.set_xlim(*_mz_limits(all_mz, np.concatenate(shown_rel)))
        ax.set_xlabel("m/z")
        ax.legend(loc="upper right", fontsize=8)
        self.canvas_match.draw_idle()

    # ---------------------------------------------------------------- settings
    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(self.settings, indent=2))
        except OSError:
            pass


def _mz_limits(mz, rel, min_rel: float = 1.0) -> tuple[float, float]:
    """x-limits covering all peaks of at least ``min_rel`` % intensity."""
    significant = mz[rel >= min_rel]
    if len(significant) == 0:
        significant = mz
    return float(significant.min()) - 5, float(significant.max()) + 10


def launch_ms_viewer(data_path=None, library_path=None, modulation=None):
    """
    Launch the GCxGC-MS viewer.

    Parameters
    ----------
    data_path : str or Path, optional
        GC-MS file to open (ANDI/AIA ``.cdf``, OpenChrom ``.csv``, ``.jdx``).
    library_path : str or Path, optional
        NIST ``MAINLIB`` folder, ``.msp``/``.zip`` file or ``.npz`` cache.
    modulation : float, optional
        Modulation time in seconds (estimated from the data if omitted).
    """
    root = tk.Tk()
    MSViewer(root, data_path, library_path, modulation)
    root.mainloop()


def main(argv=None):
    """Command line entry point (``pygcxgc-msviewer``)."""
    parser = argparse.ArgumentParser(description="pyGCxGC GCxGC-MS viewer")
    parser.add_argument("data", nargs="?", help="GC-MS file (.cdf, .csv, .jdx)")
    parser.add_argument(
        "-l", "--library", help="NIST MAINLIB folder, .msp/.zip or .npz library"
    )
    parser.add_argument(
        "-m", "--modulation", type=float, help="modulation time in seconds"
    )
    args = parser.parse_args(argv)
    launch_ms_viewer(args.data, args.library, args.modulation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
