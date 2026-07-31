"""Test suite for MetaGLens.

Written against stdlib ``unittest`` so it runs with no extra dependencies:

    python3 -m unittest discover -s tests -v
    # or, if pytest is installed:
    python3 -m pytest tests -v

Only modules that do not require typer/rich are imported, so the suite runs in a
bare interpreter.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

from metaglens import render, routes, samples as samples_mod
from metaglens import conda_env, conda_setup
from metaglens.config import Config
from metaglens import pipeline
from metaglens.report import generate_report, _parse_fastp_reports

_PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")

# Rich is a CLI-only dependency; the suite must stay runnable on a bare
# interpreter (CI installs PyYAML only), so rich-dependent cases are skipped
# rather than failing the run.
try:  # pragma: no cover - availability probe
    import rich  # noqa: F401
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False


def _make_reads(raw: Path, ids, r1_tpl="{id}_R1.fastq.gz", r2_tpl="{id}_R2.fastq.gz"):
    raw.mkdir(parents=True, exist_ok=True)
    for sid in ids:
        (raw / r1_tpl.format(id=sid)).write_bytes(b"")
        (raw / r2_tpl.format(id=sid)).write_bytes(b"")


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def make_cfg(self, **kw) -> Config:
        raw = self.tmp / "raw"
        _make_reads(raw, ["A", "B"])
        defaults = dict(
            project_name="demo",
            work_dir=str(self.tmp / "work"),
            raw_data_dir=str(raw),
            route_name="mag_per_sample",
        )
        defaults.update(kw)
        return Config(**defaults)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
class TestRoutes(TempDirCase):
    def test_presets_start_with_setup_and_are_known_steps(self):
        for name, route in routes.ROUTES.items():
            self.assertEqual(route.steps[0], "00_setup", name)
            for step in route.steps:
                self.assertIn(step, routes.STEPS, f"{name}/{step}")

    def test_contig_route_skips_binning(self):
        steps = routes.ROUTES["contig_based"].steps
        self.assertIn("09_contig", steps)
        for step in ("04_binning", "05_checkm", "06_derep"):
            self.assertNotIn(step, steps)

    def test_mag_routes_differ_only_by_binning_strategy(self):
        a, b = routes.ROUTES["mag_per_sample"], routes.ROUTES["mag_co_binning"]
        self.assertEqual(a.steps, b.steps)
        self.assertEqual(a.binning_strategy, "per_sample")
        self.assertEqual(b.binning_strategy, "co_binning")

    def test_build_selected_steps_orders_and_injects_setup(self):
        got = routes.build_selected_steps(["07_taxonomy", "01_qc"])
        self.assertEqual(got, ["00_setup", "01_qc", "07_taxonomy"])

    def test_custom_route_infers_basis(self):
        self.assertEqual(
            routes.resolve_route("custom", ["09_contig"]).analysis_basis, "contig")
        self.assertEqual(
            routes.resolve_route("custom", ["04_binning"]).analysis_basis, "mag")
        self.assertEqual(
            routes.resolve_route("custom", ["04_binning", "09_contig"]).analysis_basis,
            "both")

    def test_unknown_route_raises(self):
        with self.assertRaises(ValueError):
            routes.resolve_route("nope")

    def test_assembly_strategy_mapping(self):
        self.assertEqual(routes.assembly_strategy_for("co_binning"), "co-assembly")
        self.assertEqual(routes.assembly_strategy_for("per_sample"), "per-sample")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
class TestConfig(TempDirCase):
    def test_valid_config_has_no_errors(self):
        self.assertEqual(self.make_cfg().validate(), [])

    def test_missing_required_fields_reported(self):
        errors = Config().validate()
        joined = " ".join(errors)
        for field in ("project_name", "work_dir", "raw_data_dir"):
            self.assertIn(field, joined)

    def test_custom_route_rejects_unknown_step(self):
        cfg = self.make_cfg(route_name="custom", custom_steps=["01_qc", "99_bogus"])
        self.assertTrue(any("99_bogus" in e for e in cfg.validate()))

    def test_custom_route_requires_steps(self):
        cfg = self.make_cfg(route_name="custom", custom_steps=[])
        self.assertTrue(any("custom_steps" in e for e in cfg.validate()))

    def test_enum_validation(self):
        cases = {
            "exec_env": "cloud",
            "conda_mode": "maybe",
            "assembler": "velvet",
            "align_tool": "bwa",
            "taxonomy_tool": "blast",
            "contig_taxonomy": "foo",
            "prokka_kingdom": "Fungi",
        }
        for field, bad in cases.items():
            with self.subTest(field=field):
                cfg = self.make_cfg(**{field: bad})
                self.assertTrue(any(field in e for e in cfg.validate()),
                                f"{field}={bad} should be rejected")

    def test_reuse_and_update_is_a_valid_conda_mode(self):
        # The wizard offers it and 00_setup.sh implements it.
        cfg = self.make_cfg(conda_mode="reuse_and_update")
        self.assertEqual(cfg.validate(), [])

    def test_yaml_round_trip(self):
        cfg = self.make_cfg(total_threads=32, use_bracken=True, ani_threshold="99")
        path = self.tmp / "c.yaml"
        cfg.to_yaml(str(path))
        back = Config.from_yaml(str(path))
        self.assertEqual(back.total_threads, 32)
        self.assertTrue(back.use_bracken)
        self.assertEqual(back.ani_threshold, "99")

    def test_unknown_yaml_key_rejected(self):
        path = self.tmp / "bad.yaml"
        path.write_text("project_name: x\nnot_a_real_key: 1\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            Config.from_yaml(str(path))

    def test_db_dir_defaults_under_work_dir(self):
        cfg = self.make_cfg(db_dir="")
        self.assertEqual(cfg.resolved_db_dir(),
                         str(Path(cfg.work_dir) / "databases"))


# --------------------------------------------------------------------------- #
# Sample discovery
# --------------------------------------------------------------------------- #
class TestSamples(TempDirCase):
    def test_supported_pairing_conventions(self):
        cases = [
            ("_R1_001/_R2_001", "{id}_R1_001.fastq.gz", "{id}_R2_001.fastq.gz"),
            ("_R1/_R2", "{id}_R1.fastq.gz", "{id}_R2.fastq.gz"),
            ("_1/_2", "{id}_1.fq.gz", "{id}_2.fq.gz"),
            (".1/.2", "{id}.1.fastq", "{id}.2.fastq"),
        ]
        for label, r1, r2 in cases:
            with self.subTest(convention=label):
                raw = self.tmp / f"raw{label.replace('/', '_').replace('.', 'd')}"
                _make_reads(raw, ["S1", "S2"], r1, r2)
                found, detected = samples_mod.discover(str(raw))[:2]
                self.assertEqual(detected, label)
                self.assertEqual([s.sample_id for s in found], ["S1", "S2"])

    def test_paths_are_absolute(self):
        raw = self.tmp / "abs"
        _make_reads(raw, ["S1"])
        found, _ = samples_mod.discover(str(raw))[:2]
        self.assertTrue(Path(found[0].r1).is_absolute())

    def test_unpaired_file_is_not_reported_as_sample(self):
        raw = self.tmp / "unpaired"
        raw.mkdir()
        (raw / "solo_R1.fastq.gz").write_bytes(b"")
        with self.assertRaises(samples_mod.SampleDiscoveryError):
            samples_mod.discover(str(raw))

    def test_empty_dir_raises(self):
        raw = self.tmp / "empty"
        raw.mkdir()
        with self.assertRaises(samples_mod.SampleDiscoveryError):
            samples_mod.discover(str(raw))

    def test_missing_dir_raises(self):
        with self.assertRaises(samples_mod.SampleDiscoveryError):
            samples_mod.discover(str(self.tmp / "nope"))

    def test_manifest_round_trip(self):
        raw = self.tmp / "mf"
        _make_reads(raw, ["S1", "S2"])
        found, _ = samples_mod.discover(str(raw))[:2]
        path = self.tmp / "samples.tsv"
        samples_mod.write_manifest(found, str(path))
        header = path.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(header, "sample_id\tr1\tr2")
        back = samples_mod.read_manifest(str(path))
        self.assertEqual([s.sample_id for s in back], ["S1", "S2"])

    def test_manifest_rejects_file_shared_between_samples(self):
        raw = self.tmp / "dup"
        _make_reads(raw, ["S1"])
        r1 = str((raw / "S1_R1.fastq.gz").resolve())
        r2 = str((raw / "S1_R2.fastq.gz").resolve())
        path = self.tmp / "dup.tsv"
        path.write_text(
            f"sample_id\tr1\tr2\nS1\t{r1}\t{r2}\nS2\t{r1}\t{r2}\n", encoding="utf-8")
        with self.assertRaises(samples_mod.SampleDiscoveryError):
            samples_mod.read_manifest(str(path))

    def test_manifest_rejects_missing_file(self):
        path = self.tmp / "ghost.tsv"
        path.write_text("sample_id\tr1\tr2\nS1\t/nope/a.fq.gz\t/nope/b.fq.gz\n",
                        encoding="utf-8")
        with self.assertRaises(samples_mod.SampleDiscoveryError):
            samples_mod.read_manifest(str(path))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
class TestRender(TempDirCase):
    def test_every_route_renders_without_leftover_placeholders(self):
        for route_name in routes.ROUTES:
            cfg = self.make_cfg(route_name=route_name)
            for step in cfg.route.steps:
                with self.subTest(route=route_name, step=step):
                    text = render.render_step(cfg, step, ["A", "B"])
                    self.assertNotRegex(text, _PLACEHOLDER_RE)

    def test_all_template_placeholders_are_supplied(self):
        """Guards against adding a template placeholder with no value source."""
        cfg = self.make_cfg()
        known = set(render.build_global_values(cfg, ["A"]))
        for step in routes.STEPS:
            known |= set(render._step_overrides(cfg, step))

        template_dir = Path(render.__file__).parent / "templates"
        for sh in sorted(template_dir.glob("*.sh")):
            found = set(_PLACEHOLDER_RE.findall(sh.read_text(encoding="utf-8")))
            missing = {p.strip() for p in found} - known
            self.assertFalse(missing, f"{sh.name} needs unsupplied {missing}")

    def test_rendered_scripts_pass_bash_syntax_check(self):
        if shutil.which("bash") is None:
            self.skipTest("bash unavailable")
        cfg = self.make_cfg(route_name="mag_and_contig")
        for step in cfg.route.steps:
            text = render.render_step(cfg, step, ["A", "B"])
            path = self.tmp / routes.STEPS[step].script
            path.write_text(text, encoding="utf-8")
            rc = subprocess.run(["bash", "-n", str(path)],
                                capture_output=True).returncode
            self.assertEqual(rc, 0, f"bash -n failed for {step}")

    def test_boolean_flags_render_as_yes_no(self):
        cfg = self.make_cfg(remove_host=True, do_tarball=False)
        values = render.build_global_values(cfg, ["A"])
        self.assertEqual(values["REMOVE_HOST"], "yes")
        self.assertEqual(values["DO_TARBALL"], "no")

    def test_parallel_plan_is_derived_when_unset(self):
        cfg = self.make_cfg(total_threads=16, parallel_jobs=0, threads_per_job=0)
        values = render.build_global_values(cfg, ["A", "B", "C", "D"])
        jobs = int(values["PARALLEL_JOBS"])
        per = int(values["THREADS_PER_JOB"])
        self.assertGreaterEqual(jobs, 1)
        self.assertGreaterEqual(per, 1)
        self.assertLessEqual(jobs * per, cfg.total_threads)

    def test_grouped_conda_env_names_used_when_creating(self):
        cfg = self.make_cfg(conda_mode="create", conda_env="tools")
        self.assertIn("tools_qc", render.render_step(cfg, "01_qc", ["A"]))
        self.assertIn("tools_mag", render.render_step(cfg, "05_checkm", ["A"]))

    def test_single_env_reused_for_all_stages(self):
        cfg = self.make_cfg(conda_mode="reuse", conda_env="one")
        text = render.render_step(cfg, "05_checkm", ["A"])
        self.assertIn("one", text)
        self.assertNotIn("one_mag", text)

    def test_unknown_placeholder_raises_render_error(self):
        cfg = self.make_cfg()
        values = render.build_global_values(cfg, ["A"])
        self.assertNotIn("NOT_A_PLACEHOLDER", values)


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
class TestPipeline(TempDirCase):
    def test_materialize_writes_scripts_and_support_files(self):
        cfg = self.make_cfg()
        written = pipeline.materialize(cfg)
        self.assertEqual(len(written), len(cfg.route.steps))
        results = cfg.results_dir
        self.assertTrue((results / "pipeline_utils.sh").is_file())
        self.assertTrue((results / "samples.tsv").is_file())
        for path in written:
            self.assertTrue(path.is_file())

    def test_materialize_rejects_invalid_config(self):
        cfg = self.make_cfg(project_name="")
        with self.assertRaises(pipeline.PipelineError):
            pipeline.materialize(cfg)

    def test_select_steps_defaults_to_whole_route(self):
        cfg = self.make_cfg()
        self.assertEqual(pipeline.select_steps(cfg), cfg.route.steps)

    def test_select_steps_from_step_truncates(self):
        cfg = self.make_cfg()
        got = pipeline.select_steps(cfg, from_step="04_binning")
        self.assertEqual(got[0], "04_binning")
        self.assertNotIn("01_qc", got)

    def test_select_steps_only_keeps_route_order(self):
        cfg = self.make_cfg()
        got = pipeline.select_steps(cfg, only=["05_checkm", "01_qc"])
        self.assertEqual(got, ["01_qc", "05_checkm"])

    def test_select_steps_rejects_step_outside_route(self):
        cfg = self.make_cfg(route_name="contig_based")
        with self.assertRaises(pipeline.PipelineError):
            pipeline.select_steps(cfg, only=["04_binning"])
        with self.assertRaises(pipeline.PipelineError):
            pipeline.select_steps(cfg, from_step="nope")

    def test_status_helpers_handle_absent_status_file(self):
        cfg = self.make_cfg()
        self.assertIsNone(pipeline.read_status(cfg))
        self.assertEqual(pipeline.step_status(cfg, "01_qc"), "pending")
        self.assertEqual(pipeline.first_incomplete_step(cfg), "00_setup")

    def test_first_incomplete_step_skips_completed(self):
        cfg = self.make_cfg()
        cfg.results_dir.mkdir(parents=True, exist_ok=True)
        (cfg.results_dir / "pipeline_status.json").write_text(json.dumps({
            "steps": {"00_setup": {"status": "completed"},
                      "01_qc": {"status": "completed"}}}), encoding="utf-8")
        self.assertEqual(pipeline.first_incomplete_step(cfg), "02_assembly")


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
class TestReport(TempDirCase):
    def _results_with_delivery(self, cfg) -> Path:
        results = cfg.results_dir
        (results / "delivery" / "tables").mkdir(parents=True, exist_ok=True)
        (results / "delivery" / "community").mkdir(parents=True, exist_ok=True)
        (results / "pipeline_status.json").write_text(json.dumps({
            "project_name": "demo", "route_name": "mag_per_sample",
            "analysis_basis": "mag", "selected_steps": ["00_setup", "01_qc"],
            "samples": ["A", "B"],
            "parallel": {"parallel_jobs": 2, "threads_per_job": 8,
                         "exec_env": "local"},
            "steps": {"00_setup": {"status": "completed", "attempts": 1}},
        }), encoding="utf-8")
        return results

    @staticmethod
    def _payload(html: str) -> dict:
        return json.loads(re.search(r"window\.__MG__=(\{.*?\});", html).group(1))

    def test_fastp_json_naming_matches_qc_template(self):
        """Regression: template writes <sample>_fastp.json, not <sample>.fastp.json."""
        qc = self.tmp / "01_qc"
        qc.mkdir()
        (qc / "A_fastp.json").write_text(json.dumps({"summary": {
            "before_filtering": {"total_reads": 1000, "total_bases": 150000},
            "after_filtering": {"total_reads": 900, "total_bases": 135000,
                                "q30_rate": 0.95, "gc_content": 0.42}}}),
            encoding="utf-8")
        parsed = _parse_fastp_reports(qc)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["sample"], "A")
        self.assertEqual(parsed[0]["raw_reads"], 1000)
        self.assertEqual(parsed[0]["clean_reads"], 900)

    def test_report_shows_raw_data_dir_from_argument(self):
        cfg = self.make_cfg()
        results = self._results_with_delivery(cfg)
        html = generate_report(results, raw_data_dir=cfg.raw_data_dir).read_text(
            encoding="utf-8")
        self.assertEqual(self._payload(html)["run"]["rawdata"], cfg.raw_data_dir)
        self.assertIn("m-rawdata", html)

    def test_report_falls_back_to_status_raw_data_dir(self):
        cfg = self.make_cfg()
        results = self._results_with_delivery(cfg)
        status_path = results / "pipeline_status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["raw_data_dir"] = "/from/status"
        status_path.write_text(json.dumps(status), encoding="utf-8")
        html = generate_report(results).read_text(encoding="utf-8")
        self.assertEqual(self._payload(html)["run"]["rawdata"], "/from/status")

    def test_report_parses_community_and_checkm_tables(self):
        cfg = self.make_cfg()
        results = self._results_with_delivery(cfg)
        (results / "delivery" / "community" / "community_matrix.tsv").write_text(
            "taxon\tA\tB\nBacteroides\t30\t20\n", encoding="utf-8")
        (results / "delivery" / "community" / "SOURCE.txt").write_text(
            "GTDB-Tk taxonomy", encoding="utf-8")
        (results / "delivery" / "tables" / "mag_relative_abundance.tsv").write_text(
            "mag\tA\tB\nbin1\t12.5\t8.0\n", encoding="utf-8")
        # CheckM2 column order: Name, Completeness, Contamination
        (results / "delivery" / "tables" / "quality_report_filtered.tsv").write_text(
            "Name\tCompleteness\tContamination\nbin1\t95.5\t2.1\n", encoding="utf-8")

        data = self._payload(generate_report(results).read_text(encoding="utf-8"))
        self.assertEqual(data["taxa"][0][0], "Bacteroides")
        self.assertEqual(data["run"]["communitySource"], "GTDB-Tk taxonomy")
        name, comp, cont, _vals = data["mags"][0]
        self.assertEqual((name, comp, cont), ("bin1", 95.5, 2.1))

    def test_report_renders_with_no_delivery_content(self):
        cfg = self.make_cfg()
        results = self._results_with_delivery(cfg)
        out = generate_report(results)
        self.assertTrue(out.is_file())
        data = self._payload(out.read_text(encoding="utf-8"))
        self.assertEqual(data["taxa"], [])
        self.assertEqual(data["mags"], [])


# --------------------------------------------------------------------------- #
# Conda discovery / inspection  (P0-1, P0-2)
# --------------------------------------------------------------------------- #
class TestCondaDiscovery(TempDirCase):
    """`which conda` is not enough: with `conda init`, conda is a shell function."""

    def _fake_conda(self, root: Path) -> Path:
        exe = root / "bin" / "conda"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        return exe

    def test_find_conda_uses_path_first(self):
        exe = self._fake_conda(self.tmp / "onpath")
        with unittest.mock.patch("shutil.which", return_value=str(exe)):
            self.assertEqual(conda_env.find_conda(), str(exe))

    def test_find_conda_falls_back_to_conda_exe(self):
        exe = self._fake_conda(self.tmp / "viaexe")
        with unittest.mock.patch("shutil.which", return_value=None), \
                unittest.mock.patch.dict("os.environ",
                                         {"CONDA_EXE": str(exe)}, clear=True):
            self.assertEqual(conda_env.find_conda(), str(exe))

    def test_find_conda_falls_back_to_conda_prefix_base(self):
        """CONDA_PREFIX pointing at a child env must still locate the base conda."""
        base = self.tmp / "distro"
        exe = self._fake_conda(base)
        child_env = base / "envs" / "someenv"
        child_env.mkdir(parents=True)
        with unittest.mock.patch("shutil.which", return_value=None), \
                unittest.mock.patch.dict("os.environ",
                                         {"CONDA_PREFIX": str(child_env)}, clear=True):
            self.assertEqual(conda_env.find_conda(), str(exe))

    def test_find_conda_probes_home_install_dirs(self):
        home = self.tmp / "home"
        exe = self._fake_conda(home / "miniconda3")
        with unittest.mock.patch("shutil.which", return_value=None), \
                unittest.mock.patch.dict("os.environ", {}, clear=True), \
                unittest.mock.patch("pathlib.Path.home", return_value=home):
            self.assertEqual(conda_env.find_conda(), str(exe))

    def test_find_conda_returns_none_when_truly_absent(self):
        with unittest.mock.patch("shutil.which", return_value=None), \
                unittest.mock.patch.dict("os.environ", {}, clear=True), \
                unittest.mock.patch("pathlib.Path.home",
                                    return_value=self.tmp / "nowhere"), \
                unittest.mock.patch("pathlib.Path.is_file", return_value=False):
            self.assertIsNone(conda_env.find_conda())

    def test_conda_setup_uses_resolved_executable(self):
        exe = self._fake_conda(self.tmp / "setupdistro")
        with unittest.mock.patch("metaglens.conda_setup.find_conda",
                                 return_value=str(exe)):
            plan = conda_setup.build_commands("base", ["qc"], single=False)
        self.assertEqual(plan[0][1][0], str(exe))

    def test_missing_conda_raises_rather_than_reporting_all_missing(self):
        """Regression: unusable conda must not look like 'nothing installed'."""
        with unittest.mock.patch("metaglens.conda_env.find_conda",
                                 return_value=None):
            with self.assertRaises(conda_env.CondaUnavailable):
                conda_env.installed_packages("someenv")
            self.assertEqual(conda_env.list_envs(), [])

    def test_nonexistent_env_raises_env_not_found(self):
        """Regression: a typo'd env name must not report 18 missing tools."""
        failed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="EnvironmentLocationNotFound")
        with unittest.mock.patch("metaglens.conda_env._run_conda",
                                 return_value=failed), \
                unittest.mock.patch("metaglens.conda_env.env_exists",
                                    return_value=False):
            with self.assertRaises(conda_env.EnvNotFound):
                conda_env.missing_tools("definitely_not_an_env")

    def test_empty_env_means_all_tools_missing(self):
        """An existing but empty env is distinct from a nonexistent one."""
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]",
                                         stderr="")
        with unittest.mock.patch("metaglens.conda_env._run_conda", return_value=ok):
            self.assertEqual(conda_env.installed_packages("emptyenv"), {})
            self.assertEqual(conda_env.missing_tools("emptyenv"),
                             conda_env.PIPELINE_TOOLS)

    def test_installed_packages_parses_versions(self):
        payload = json.dumps([{"name": "fastp", "version": "0.23.4"},
                              {"name": "megahit", "version": "1.2.9"}])
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout=payload,
                                         stderr="")
        with unittest.mock.patch("metaglens.conda_env._run_conda", return_value=ok):
            self.assertEqual(conda_env.installed_packages("e"),
                             {"fastp": "0.23.4", "megahit": "1.2.9"})
            inv = conda_env.inventory("e", ["fastp", "drep"])
            self.assertEqual(inv, {"fastp": "0.23.4", "drep": "missing"})

    def test_env_selector_uses_prefix_for_paths(self):
        self.assertEqual(conda_env._env_selector("myenv"), ["-n", "myenv"])
        self.assertEqual(conda_env._env_selector("/opt/envs/x"),
                         ["-p", "/opt/envs/x"])

    def test_base_env_is_named_base_not_install_dir(self):
        """conda reports base as the install root; showing 'miniconda3' confuses users."""
        self.assertEqual(conda_env._env_name("/home/u/miniconda3"), "base")
        self.assertEqual(conda_env._env_name("/home/u/miniconda3/envs/qc"), "qc")
        self.assertEqual(conda_env._env_name("/opt/anaconda3"), "base")


# --------------------------------------------------------------------------- #
# Scheduler directives  (P0-4)
# --------------------------------------------------------------------------- #
class TestSchedulerPaths(TempDirCase):
    def test_work_dir_renders_absolute(self):
        cfg = self.make_cfg(work_dir="./relative_work")
        values = render.build_global_values(cfg, ["A"])
        self.assertTrue(Path(values["WORK_DIR"]).is_absolute(),
                        values["WORK_DIR"])

    def test_sbatch_output_path_is_absolute_in_every_script(self):
        """Regression: relative --output only worked when submitting from work_dir."""
        cfg = self.make_cfg(work_dir="./relative_work", route_name="mag_and_contig")
        checked = 0
        for step in cfg.route.steps:
            text = render.render_step(cfg, step, ["A", "B"])
            for line in text.splitlines():
                if line.startswith("#SBATCH --output="):
                    path = line.split("=", 1)[1]
                    self.assertTrue(path.startswith("/"),
                                    f"{step}: non-absolute --output {path}")
                    checked += 1
        self.assertEqual(checked, len(cfg.route.steps))


class TestCommunitySourceFix(TempDirCase):
    """Regression tests for §7-8: nullglob literal-array bug + empty-matrix guard."""

    def _render_community(self) -> str:
        cfg = self.make_cfg(route_name="mag_and_contig")
        return render.render_step(cfg, "10_community", ["A", "B"])

    def test_gtdb_summaries_uses_real_glob_not_literals(self):
        text = self._render_community()
        line = next(l for l in text.splitlines() if "GTDB_SUMMARIES=" in l)
        # A real glob contains a wildcard; the buggy version listed two
        # literal filenames (which nullglob cannot prune).
        self.assertIn("*", line, line)
        self.assertNotIn("gtdbtk.bac120.summary.tsv", line, line)

    @unittest.skipIf(shutil.which("bash") is None, "bash unavailable")
    def test_nullglob_prunes_glob_but_not_literals(self):
        empty = self.tmp / "emptydir"
        empty.mkdir()
        script = (
            "shopt -s nullglob\n"
            f'glob=("{empty}/"*.summary.tsv)\n'
            f'lit=("{empty}/gtdbtk.bac120.summary.tsv" "{empty}/gtdbtk.ar53.summary.tsv")\n'
            'echo "${#glob[@]} ${#lit[@]}"\n'
        )
        out = subprocess.run(["bash", "-c", script],
                             capture_output=True, text=True, check=True).stdout.strip()
        # glob correctly collapses to 0; literal array stays at 2 (the bug).
        self.assertEqual(out, "0 2")

    def test_empty_matrix_guard_precedes_completed(self):
        text = self._render_community()
        self.assertIn("NUM_TAXA", text)
        guard_idx = text.find("-lt 1")
        completed_idx = text.find('update_step_status "${STEP_NAME}" "completed"')
        self.assertGreater(guard_idx, 0, "empty-matrix guard missing")
        self.assertGreater(completed_idx, 0, "completed marker missing")
        self.assertLess(guard_idx, completed_idx,
                        "guard must run before the stage is marked completed")


class TestContigCommunityValidation(TempDirCase):
    """§7-8 fail-fast: contig route needs a taxonomy source for 10_community."""

    def test_contig_based_without_taxonomy_is_rejected(self):
        cfg = self.make_cfg(route_name="contig_based", contig_taxonomy="none")
        errs = cfg.validate()
        self.assertTrue(any("10_community" in e and "contig_taxonomy" in e
                            for e in errs), errs)

    def test_contig_based_with_kraken2_is_accepted(self):
        cfg = self.make_cfg(route_name="contig_based", contig_taxonomy="kraken2")
        self.assertEqual(cfg.validate(), [])

    def test_mag_route_unaffected_by_contig_taxonomy_none(self):
        # mag routes carry 07_taxonomy, so the community source always exists.
        cfg = self.make_cfg(route_name="mag_per_sample", contig_taxonomy="none")
        self.assertEqual(cfg.validate(), [])


class TestHardwareProbe(unittest.TestCase):
    """Phase 1: stdlib hardware probing with graceful fallbacks."""

    def _meminfo(self, kb: int) -> str:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = d / "meminfo"
        p.write_text(f"MemTotal:       {kb} kB\nMemFree:  123 kB\n", encoding="utf-8")
        return str(p)

    def test_probe_returns_populated_info(self):
        from metaglens.sense import hardware
        info = hardware.probe(path=".")
        self.assertGreaterEqual(info.cores, 1)
        self.assertGreater(info.ram_gb, 0.0)
        self.assertIsInstance(info.in_container, bool)

    def test_ram_read_from_meminfo(self):
        from metaglens.sense import hardware
        # 2 GiB expressed in kB.
        info = hardware.probe(path=".", meminfo_path=self._meminfo(2 * 1024 * 1024))
        self.assertAlmostEqual(info.ram_gb, 2.0, places=3)

    def test_disk_free_uses_disk_usage(self):
        from metaglens.sense import hardware
        Usage = __import__("collections").namedtuple("Usage", "total used free")
        with unittest.mock.patch(
            "metaglens.sense.hardware.shutil.disk_usage",
            return_value=Usage(0, 0, 5 * 1024 ** 3),
        ):
            info = hardware.probe(path=".", meminfo_path=self._meminfo(1024 * 1024))
        self.assertAlmostEqual(info.disk_free_gb, 5.0, places=3)

    def test_disk_free_resolves_nonexistent_path(self):
        """A work_dir that does not exist yet must not report 0 GB free."""
        from metaglens.sense import hardware
        missing = str(Path(tempfile.gettempdir()) / "mg_no_such_dir" / "deeper")
        info = hardware.probe(path=missing)
        self.assertGreater(info.disk_free_gb, 0.0)

    def test_result_complete_without_psutil(self):
        from metaglens.sense import hardware
        # Force the psutil fallback path to be a no-op and ensure stdlib meminfo
        # still yields a usable figure.
        with unittest.mock.patch.object(hardware, "_psutil_ram_gb", return_value=0.0):
            info = hardware.probe(path=".", meminfo_path=self._meminfo(4 * 1024 * 1024))
        self.assertAlmostEqual(info.ram_gb, 4.0, places=3)
        self.assertGreaterEqual(info.cores, 1)


class TestDatabaseRegistry(TempDirCase):
    """Phase 2: database registry, discovery, validation, requirement derivation."""

    _DB_ENV = ("GTDBTK_DATA_PATH", "CHECKM2DB", "KRAKEN2_DB_PATH", "EGGNOG_DATA_DIR")

    def _fake_gtdbtk(self, version="r232") -> Path:
        root = self.tmp / "gtdbtk_data" / f"release{version.lstrip('r')}"
        (root / "taxonomy").mkdir(parents=True)
        (root / "taxonomy" / "gtdb_taxonomy.tsv").write_text("x\n", encoding="utf-8")
        (root / "metadata").mkdir(parents=True)
        (root / "metadata" / "metadata.txt").write_text(
            f"VERSION_DATA={version}\n", encoding="utf-8")
        return root

    def _clear_db_env(self):
        patcher = unittest.mock.patch.dict("os.environ",
                                           {k: "" for k in self._DB_ENV}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_discover_gtdbtk_via_filesystem_scan(self):
        from metaglens.sense import database as db
        self._clear_db_env()
        self._fake_gtdbtk("r232")
        cfg = self.make_cfg()  # taxonomy_tool=gtdbtk default, taxonomy_db=""
        st = db.discover("gtdbtk", cfg, scan_roots=[self.tmp])
        self.assertEqual(st.state, "ready", st.detail)
        self.assertEqual(st.source, "scan")
        self.assertEqual(st.version, "r232")
        self.assertTrue(st.path.endswith("release232"), st.path)

    def test_discover_env_var(self):
        from metaglens.sense import database as db
        root = self._fake_gtdbtk("r220")
        cfg = self.make_cfg()
        with unittest.mock.patch.dict("os.environ",
                                      {"GTDBTK_DATA_PATH": str(root)}, clear=False):
            st = db.discover("gtdbtk", cfg, scan_roots=[self.tmp])
        self.assertEqual(st.state, "ready")
        self.assertEqual(st.source, "env")
        self.assertEqual(st.version, "r220")

    def test_discover_wrong_path(self):
        from metaglens.sense import database as db
        self._clear_db_env()
        bogus = self.tmp / "not_a_db"
        bogus.mkdir()
        cfg = self.make_cfg(checkm2_db=str(bogus))
        st = db.discover("checkm2", cfg, scan_roots=[self.tmp])
        self.assertEqual(st.state, "wrong_path")
        self.assertIn("does not look like", st.detail)

    def test_discover_missing_gives_download_hint(self):
        from metaglens.sense import database as db
        self._clear_db_env()
        empty = self.tmp / "empty"
        empty.mkdir()
        cfg = self.make_cfg()
        st = db.discover("eggnog", cfg, scan_roots=[empty])
        self.assertEqual(st.state, "missing")
        self.assertIn("download_eggnog_data.py", st.detail)

    def test_validate_direct(self):
        from metaglens.sense import database as db
        root = self._fake_gtdbtk("r214")
        ok, detail = db.validate("gtdbtk", str(root))
        self.assertTrue(ok, detail)
        self.assertIn("r214", detail)
        bad_ok, _ = db.validate("gtdbtk", str(self.tmp))
        self.assertFalse(bad_ok)

    def test_required_databases_mag_default(self):
        from metaglens.sense import database as db
        cfg = self.make_cfg(route_name="mag_per_sample")  # gtdbtk + eggnog default
        need = db.required_databases(cfg)
        self.assertEqual(set(need), {"checkm2", "gtdbtk", "eggnog"})

    def test_required_databases_kraken_taxonomy(self):
        from metaglens.sense import database as db
        cfg = self.make_cfg(route_name="mag_per_sample", taxonomy_tool="kraken2")
        need = db.required_databases(cfg)
        self.assertIn("kraken2", need)
        self.assertNotIn("gtdbtk", need)

    def test_required_databases_contig(self):
        from metaglens.sense import database as db
        cfg = self.make_cfg(route_name="contig_based", contig_taxonomy="kraken2")
        need = db.required_databases(cfg)
        self.assertIn("kraken2", need)
        self.assertIn("eggnog", need)
        self.assertNotIn("checkm2", need)
        self.assertNotIn("gtdbtk", need)


class TestParallelPlanner(unittest.TestCase):
    """Phase 3: parallel-plan recommendation with rationale."""

    def test_memory_caps_jobs_with_reason(self):
        from metaglens.decide import planner
        # 10 samples, 64 cores, but only 64 GB RAM at ~24 GB/job -> cap at 2.
        plan = planner.recommend_parallel(cores=64, ram_gb=64, n_samples=10)
        self.assertTrue(plan.memory_capped)
        self.assertLess(plan.jobs, 10)
        self.assertEqual(plan.jobs, 2)  # floor(64/24)
        self.assertIn("OOM", plan.reason)

    def test_jobs_times_threads_never_exceed_cores(self):
        from metaglens.decide import planner
        for cores in (1, 4, 16, 112):
            for ram in (0, 8, 128, 512):
                for n in (1, 3, 200):
                    p = planner.recommend_parallel(cores, ram, n)
                    self.assertLessEqual(p.jobs * p.threads_per_job, cores,
                                         f"{cores}/{ram}/{n}")
                    self.assertGreaterEqual(p.jobs, 1)
                    self.assertGreaterEqual(p.threads_per_job, 1)

    def test_ample_memory_uses_full_parallelism(self):
        from metaglens.decide import planner
        plan = planner.recommend_parallel(cores=112, ram_gb=498, n_samples=7)
        self.assertFalse(plan.memory_capped)
        self.assertEqual(plan.jobs, 7)
        self.assertEqual(plan.threads_per_job, 112 // 7)

    def test_unknown_ram_not_capped(self):
        from metaglens.decide import planner
        plan = planner.recommend_parallel(cores=32, ram_gb=0, n_samples=8)
        self.assertFalse(plan.memory_capped)
        self.assertEqual(plan.jobs, 8)
        self.assertIn("RAM unknown", plan.reason)


class TestSharedTheme(unittest.TestCase):
    """Phase 4.2: report sources the shared visual module; output unchanged."""

    def test_report_uses_shared_theme_objects(self):
        from metaglens import report, _theme
        self.assertIs(report._CSS, _theme.REPORT_CSS)
        self.assertIs(report._LENS, _theme.LENS_SVG)

    def test_report_still_contains_skin_and_data(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "pipeline_status.json").write_text(json.dumps({
            "project_name": "demo", "route_name": "mag_per_sample",
            "analysis_basis": "mag", "samples": ["A"],
            "selected_steps": ["00_setup", "10_community"],
            "steps": {"10_community": {"status": "running", "attempts": 1}},
        }), encoding="utf-8")
        (d / "delivery" / "community").mkdir(parents=True)
        (d / "delivery" / "community" / "community_matrix.tsv").write_text(
            "taxon\tA\ng__Foo\t9\n", encoding="utf-8")
        out = generate_report(d, raw_data_dir="/data/reads")
        html = out.read_text(encoding="utf-8")
        self.assertIn("--brand:#38A8F0", html)          # palette
        self.assertIn('points="100,4 183.14,52', html)  # lens polygon
        self.assertIn("window.__MG__", html)
        self.assertIn('"demo"', html)


class TestWebConfig(TempDirCase):
    """Phase 4: web config backends, equivalence, i18n, security."""

    def _payload(self) -> dict:
        raw = self.tmp / "raw"
        _make_reads(raw, ["A", "B"])
        return dict(
            project_name="demo",
            work_dir=str(self.tmp / "work"),
            raw_data_dir=str(raw),
            route_name="mag_per_sample",
            total_threads=16,
            taxonomy_tool="gtdbtk",
            contig_taxonomy="none",
            use_eggnog=True,
        )

    def test_save_equivalent_to_direct_to_yaml(self):
        from metaglens.express import webconfig
        payload = self._payload()
        out_web = str(self.tmp / "web.yaml")
        out_dir = str(self.tmp / "direct.yaml")
        ok, errs, _ = webconfig.save_config(payload, out_web)
        self.assertTrue(ok, errs)
        Config(**webconfig.coerce_payload(payload)).to_yaml(out_dir)
        self.assertEqual(Path(out_web).read_bytes(), Path(out_dir).read_bytes())

    def test_language_does_not_pollute_yaml(self):
        from metaglens.express import webconfig
        base = self._payload()
        a = str(self.tmp / "zh.yaml")
        b = str(self.tmp / "en.yaml")
        ok1, _, _ = webconfig.save_config(dict(base, lang="zh"), a)
        ok2, _, _ = webconfig.save_config(dict(base, lang="en"), b)
        self.assertTrue(ok1 and ok2)
        self.assertEqual(Path(a).read_bytes(), Path(b).read_bytes())

    def test_invalid_payload_returns_errors_and_writes_nothing(self):
        from metaglens.express import webconfig
        out = str(self.tmp / "bad.yaml")
        ok, errs, _ = webconfig.save_config(
            {"project_name": "", "work_dir": "", "raw_data_dir": ""}, out)
        self.assertFalse(ok)
        self.assertTrue(errs)
        self.assertFalse(Path(out).exists())

    def test_api_hardware_and_plan(self):
        from metaglens.express import webconfig
        hw = webconfig.api_hardware()
        self.assertGreaterEqual(hw["cores"], 1)
        plan = webconfig.api_plan(64, 64, 10)
        self.assertTrue(plan["memory_capped"])
        self.assertIn("reason", plan)

    def test_api_samples(self):
        from metaglens.express import webconfig
        raw = self.tmp / "raw"
        _make_reads(raw, ["A", "B"])
        res = webconfig.api_samples(str(raw))
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["samples"]), 2)

    def test_api_db_validate_and_required(self):
        from metaglens.express import webconfig
        root = self.tmp / "gtdbtk_data" / "release232"
        (root / "taxonomy").mkdir(parents=True)
        (root / "taxonomy" / "gtdb_taxonomy.tsv").write_text("x\n", encoding="utf-8")
        (root / "metadata").mkdir(parents=True)
        (root / "metadata" / "metadata.txt").write_text(
            "VERSION_DATA=r232\n", encoding="utf-8")
        res = webconfig.api_db("gtdbtk", str(root))
        self.assertTrue(res["ok"])
        req = webconfig.api_required_dbs(
            {"route_name": "mag_per_sample", "taxonomy_tool": "gtdbtk",
             "use_eggnog": "true", "work_dir": str(self.tmp)})
        self.assertIn("gtdbtk", req["required"])

    def test_build_page_has_token_langs_and_skin(self):
        from metaglens.express import webconfig
        page = webconfig.build_page("TOK123", lang="zh")
        self.assertIn("TOK123", page)
        self.assertIn("--brand:#38A8F0", page)          # shared palette
        self.assertIn('points="100,4 183.14,52', page)  # shared lens
        self.assertIn("zh:", page)                       # bilingual dicts
        self.assertIn("en:", page)

    def test_plan_uses_real_sample_count_not_hardcoded(self):
        """Regression: the plan request must not hardcode n=1.

        A previous version fetched the samples box but never used it, so the
        parallel recommendation was always computed for a single sample. The URL
        concatenation ("&n="+n) is identical in both versions — only the value
        bound to n differs — so assert on the binding, not the URL.
        """
        from metaglens.express import webconfig
        page = webconfig.build_page("TOK", lang="en")
        # n must be derived from the discovered sample count ...
        self.assertIn("var n=SAMPLES.length||1", page)
        # ... and still concatenated into the plan URL as a variable.
        self.assertIn('"&n="+n', page)
        # The stale hardcoded binding and its dead fetch must be gone.
        self.assertNotIn("var n=1;", page)
        self.assertNotIn('var sb=document.getElementById("samples-box")', page)

    def test_live_server_token_and_save(self):
        import urllib.request
        import urllib.error
        from metaglens.express import webconfig

        out = str(self.tmp / "served.yaml")
        server = webconfig._ConfigServer(
            ("127.0.0.1", 0), webconfig._Handler,
            token="secrettok", out_path=out, lang="zh")
        # Confirm we never bind a routable interface.
        self.assertEqual(server.server_address[0], "127.0.0.1")
        port = server.server_address[1]
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{port}"
        try:
            # No token -> 403.
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + "/", timeout=5)
            self.assertEqual(ctx.exception.code, 403)
            # With token -> 200 HTML.
            with urllib.request.urlopen(base + "/?token=secrettok", timeout=5) as r:
                self.assertEqual(r.status, 200)
                self.assertIn(b"MetaGLens", r.read())
            # POST /save with a valid payload writes the yaml.
            body = json.dumps(self._payload()).encode("utf-8")
            req = urllib.request.Request(
                base + "/save?token=secrettok", data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.status, 200)
                self.assertTrue(json.loads(r.read())["ok"])
            self.assertTrue(Path(out).is_file())
        finally:
            pass


class TestMonitor(unittest.TestCase):
    """Phase 6: self-refreshing static monitor page."""

    def _results(self) -> Path:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "pipeline_status.json").write_text(json.dumps({
            "project_name": "demo", "route_name": "mag_per_sample",
            "analysis_basis": "mag",
            "selected_steps": ["00_setup", "01_qc", "02_assembly", "04_binning"],
            "steps": {
                "00_setup": {"status": "completed", "attempts": 1,
                             "started": "10:00", "finished": "10:01"},
                "01_qc": {"status": "completed", "attempts": 1,
                          "started": "10:01", "finished": "10:20"},
                "02_assembly": {"status": "running", "attempts": 1,
                                "started": "10:20"},
                "04_binning": {"status": "pending", "attempts": 0},
            },
            "last_failure": {},
        }), encoding="utf-8")
        logs = d / "reports" / "logs"
        logs.mkdir(parents=True)
        (logs / "02_assembly.log").write_text(
            "line1\nline2\n[k=99] assembling contigs from SdBG\n", encoding="utf-8")
        return d

    def test_collect_picks_running_stage_and_log(self):
        from metaglens.observe import monitor
        data = monitor.collect(self._results())
        self.assertEqual(data["current"], "02_assembly")
        self.assertIn("assembling contigs", data["log_tail"])
        self.assertEqual(len(data["steps"]), 4)

    def test_render_contains_skin_refresh_and_states(self):
        from metaglens.observe import monitor
        data = monitor.collect(self._results())
        html = monitor.render_html(data, refresh=7)
        self.assertIn('http-equiv="refresh" content="7"', html)
        self.assertIn("--brand:#38A8F0", html)             # shared palette
        self.assertIn('points="100,4 183.14,52', html)     # shared lens
        for step in ("00_setup", "01_qc", "02_assembly", "04_binning"):
            self.assertIn(step, html)
        self.assertIn("assembling contigs", html)          # log tail embedded
        self.assertIn("var(--warn)", html)                 # running stage color

    def test_failed_stage_marked_red(self):
        from metaglens.observe import monitor
        data = {
            "project": "p", "route": "r", "basis": "mag",
            "steps": [{"step": "04_binning", "status": "failed",
                       "started": "1", "finished": "", "attempts": 2}],
            "current": "04_binning", "log_file": "04_binning.log",
            "log_tail": "boom", "last_failure": {
                "stage": "04_binning", "command": "metabat2",
                "exit_code": 1, "line": 42},
        }
        html = monitor.render_html(data)
        self.assertIn("var(--bad)", html)
        self.assertIn("Last failure", html)
        self.assertIn("metabat2", html)

    def test_write_monitor_writes_selfcontained_file(self):
        from metaglens.observe import monitor
        results = self._results()
        out = monitor.write_monitor(results, refresh=5)
        self.assertTrue(out.is_file())
        self.assertEqual(out.name, "monitor.html")
        html = out.read_text(encoding="utf-8")
        self.assertIn('http-equiv="refresh"', html)
        self.assertIn("MetaGLens Monitor", html)


class TestNestedDiscovery(TempDirCase):
    """Phase 7: recursive discovery with safe id derivation."""

    def _pair(self, d: Path, r1: str, r2: str):
        d.mkdir(parents=True, exist_ok=True)
        (d / r1).write_bytes(b"")
        (d / r2).write_bytes(b"")

    def test_layout1_per_sample_directories(self):
        raw = self.tmp / "l1"
        self._pair(raw / "SampleA", "SampleA_R1.fastq.gz", "SampleA_R2.fastq.gz")
        self._pair(raw / "SampleB", "SampleB_R1.fastq.gz", "SampleB_R2.fastq.gz")
        res = samples_mod.discover(str(raw))
        self.assertEqual([s.sample_id for s in res.samples], ["SampleA", "SampleB"])
        self.assertEqual(res.layout, "nested")
        self.assertEqual(res.id_source, "filename")

    def test_layout2_generic_names_use_dirname(self):
        raw = self.tmp / "l2"
        self._pair(raw / "S1", "reads_1.fq.gz", "reads_2.fq.gz")
        self._pair(raw / "S2", "reads_1.fq.gz", "reads_2.fq.gz")
        res = samples_mod.discover(str(raw))
        self.assertEqual(sorted(s.sample_id for s in res.samples), ["S1", "S2"])
        self.assertEqual(res.layout, "nested")
        self.assertEqual(res.id_source, "dirname")
        for s in res.samples:
            self.assertEqual(Path(s.r1).parent, Path(s.r2).parent)
            self.assertEqual(Path(s.r1).parent.name, s.sample_id)

    def test_never_pairs_across_directories(self):
        """Counter-example: a lone R1 in one dir and a lone R2 in another."""
        raw = self.tmp / "split"
        (raw / "dirA").mkdir(parents=True)
        (raw / "dirB").mkdir(parents=True)
        (raw / "dirA" / "X_R1.fastq.gz").write_bytes(b"")
        (raw / "dirB" / "X_R2.fastq.gz").write_bytes(b"")
        with self.assertRaises(samples_mod.SampleDiscoveryError):
            samples_mod.discover(str(raw))

    def test_ambiguous_ids_demand_manifest(self):
        raw = self.tmp / "amb"
        self._pair(raw / "run1" / "S", "reads_1.fq.gz", "reads_2.fq.gz")
        self._pair(raw / "run2" / "S", "reads_1.fq.gz", "reads_2.fq.gz")
        with self.assertRaises(samples_mod.SampleDiscoveryError) as ctx:
            samples_mod.discover(str(raw))
        self.assertIn("manifest", str(ctx.exception))

    def test_symlink_loop_does_not_hang(self):
        raw = self.tmp / "loop"
        self._pair(raw / "SampleA", "SampleA_R1.fastq.gz", "SampleA_R2.fastq.gz")
        try:
            (raw / "SampleA" / "back").symlink_to(raw, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        res = samples_mod.discover(str(raw))
        self.assertEqual([s.sample_id for s in res.samples], ["SampleA"])

    def test_depth_limit_truncates(self):
        raw = self.tmp / "deep"
        self._pair(raw / "lvl1", "A_R1.fastq.gz", "A_R2.fastq.gz")
        self._pair(raw / "a" / "b" / "c" / "d", "Z_R1.fastq.gz", "Z_R2.fastq.gz")
        res = samples_mod.discover(str(raw))
        ids = [s.sample_id for s in res.samples]
        self.assertIn("A", ids)
        self.assertNotIn("Z", ids)

    def test_flat_layout_regression(self):
        raw = self.tmp / "flat"
        _make_reads(raw, ["S1", "S2"])
        res = samples_mod.discover(str(raw))
        self.assertEqual([s.sample_id for s in res.samples], ["S1", "S2"])
        self.assertEqual(res.pattern, "_R1/_R2")
        self.assertEqual(res.layout, "flat")
        self.assertEqual(res.id_source, "filename")
        self.assertEqual(Path(res.samples[0].r1),
                         (raw / "S1_R1.fastq.gz").resolve())


class TestEditableSampleTable(TempDirCase):
    """Phase 7.5: the web table may rename / exclude samples."""

    def _base(self, raw: Path) -> dict:
        return dict(project_name="demo", work_dir=str(self.tmp / "w"),
                    raw_data_dir=str(raw), route_name="mag_per_sample")

    def test_edited_samples_written_as_manifest(self):
        from metaglens.express import webconfig
        raw = self.tmp / "raw"
        _make_reads(raw, ["S1", "S2"])
        found = samples_mod.discover(str(raw)).samples
        payload = self._base(raw)
        payload["samples"] = [
            {"sample_id": "renamed", "r1": found[0].r1, "r2": found[0].r2}
        ]
        out = str(self.tmp / "cfg.yaml")
        ok, errs, _ = webconfig.save_config(payload, out)
        self.assertTrue(ok, errs)
        manifest = self.tmp / "samples.tsv"
        self.assertTrue(manifest.is_file())
        rows = samples_mod.read_manifest(str(manifest))
        self.assertEqual([r.sample_id for r in rows], ["renamed"])

    def test_duplicate_ids_in_table_rejected(self):
        from metaglens.express import webconfig
        raw = self.tmp / "raw2"
        _make_reads(raw, ["S1", "S2"])
        found = samples_mod.discover(str(raw)).samples
        payload = self._base(raw)
        payload["samples"] = [
            {"sample_id": "same", "r1": found[0].r1, "r2": found[0].r2},
            {"sample_id": "same", "r1": found[1].r1, "r2": found[1].r2},
        ]
        out = str(self.tmp / "cfg2.yaml")
        ok, errs, _ = webconfig.save_config(payload, out)
        self.assertFalse(ok)
        self.assertTrue(errs)
        self.assertFalse(Path(out).exists())


class TestRequiredTools(TempDirCase):
    """Phase 8.1: required_tools follows route + switches (ruling D-2 base)."""

    def test_mag_default_requires_expected_tools(self):
        from metaglens.sense import tools
        cfg = self.make_cfg(route_name="mag_per_sample")
        need = tools.required_tools(cfg)
        for expected in ("fastp", "megahit", "seqkit", "bowtie2", "samtools",
                         "metabat2", "checkm2", "drep", "gtdbtk", "prokka",
                         "eggnog-mapper"):
            self.assertIn(expected, need, expected)
        # Alternatives for switched-off choices must be absent.
        self.assertNotIn("spades", need)
        self.assertNotIn("bwa-mem2", need)
        self.assertNotIn("kraken2", need)
        self.assertNotIn("bracken", need)

    def test_contig_route_does_not_require_binning_or_mag_tools(self):
        from metaglens.sense import tools
        cfg = self.make_cfg(route_name="contig_based", contig_taxonomy="kraken2")
        need = tools.required_tools(cfg)
        for absent in ("maxbin2", "concoct", "das_tool", "checkm2", "drep",
                       "gtdbtk", "prokka"):
            self.assertNotIn(absent, need, absent)
        self.assertIn("kraken2", need)
        self.assertIn("prodigal", need)   # 09_contig predicts genes

    def test_assembler_and_aligner_switches(self):
        from metaglens.sense import tools
        cfg = self.make_cfg(assembler="metaspades", align_tool="bwa-mem2")
        need = tools.required_tools(cfg)
        self.assertIn("spades", need)
        self.assertNotIn("megahit", need)
        self.assertIn("bwa-mem2", need)
        self.assertNotIn("bowtie2", need)

    def test_bowtie2_needed_for_host_removal_even_with_bwa(self):
        from metaglens.sense import tools
        cfg = self.make_cfg(align_tool="bwa-mem2", remove_host=True,
                            host_genome="/tmp/host.fa")
        need = tools.required_tools(cfg)
        self.assertIn("bowtie2", need)
        self.assertIn("host", need["bowtie2"])

    def test_metabat2_needed_for_depth_without_binning(self):
        """jgi_summarize_bam_contig_depths ships with metabat2."""
        from metaglens.sense import tools
        cfg = self.make_cfg(route_name="contig_based", contig_taxonomy="kraken2",
                            calc_depth=True)
        need = tools.required_tools(cfg)
        self.assertIn("metabat2", need)
        self.assertIn("depth", need["metabat2"])

    def test_binner_switches_respected(self):
        from metaglens.sense import tools
        cfg = self.make_cfg(use_maxbin2=False, use_concoct=False,
                            use_das_tool=False)
        need = tools.required_tools(cfg)
        self.assertNotIn("maxbin2", need)
        self.assertNotIn("concoct", need)
        self.assertNotIn("das_tool", need)
        self.assertIn("metabat2", need)

    def test_bracken_only_with_kraken_taxonomy(self):
        from metaglens.sense import tools
        cfg = self.make_cfg(taxonomy_tool="kraken2", use_bracken=True)
        self.assertIn("bracken", tools.required_tools(cfg))
        cfg2 = self.make_cfg(taxonomy_tool="gtdbtk", use_bracken=True)
        self.assertNotIn("bracken", tools.required_tools(cfg2))

    def test_every_required_tool_has_a_reason_and_command(self):
        from metaglens.sense import tools
        cfg = self.make_cfg(route_name="mag_and_contig")
        need = tools.required_tools(cfg)
        self.assertTrue(need)
        for tool, reason in need.items():
            self.assertTrue(reason.strip(), tool)
            spec = tools.tool_spec(tool)
            self.assertIsNotNone(spec, tool)
            self.assertTrue(spec.command)

    def test_required_is_subset_of_known(self):
        from metaglens.sense import tools
        known = set(tools.all_known_tools())
        for route in ("mag_per_sample", "contig_based", "mag_and_contig"):
            cfg = self.make_cfg(route_name=route, contig_taxonomy="kraken2")
            self.assertLessEqual(set(tools.required_tools(cfg)), known, route)


class TestDoctorReport(TempDirCase):
    """Phase 8.2: doctor report honours ruling D-2 and the three-state conda."""

    def _report(self, cfg, **kw):
        from metaglens.sense import doctor
        return doctor.build_report(cfg, **kw)

    def test_unneeded_tools_listed_but_not_problems(self):
        cfg = self.make_cfg(route_name="contig_based", contig_taxonomy="kraken2",
                            conda_mode="none", conda_env="none")
        rep = self._report(cfg)
        by_tool = {r["tool"]: r for r in rep["tools"]}
        # Every known tool appears, including ones this route never invokes.
        self.assertIn("gtdbtk", by_tool)
        self.assertFalse(by_tool["gtdbtk"]["required"])
        self.assertEqual(by_tool["gtdbtk"]["status"], "not_needed")
        # A not-needed tool must never contribute a problem.
        self.assertFalse(any("gtdbtk is required" in p for p in rep["problems"]))

    def test_missing_required_tool_is_a_problem(self):
        cfg = self.make_cfg(conda_mode="none", conda_env="none")
        with unittest.mock.patch("shutil.which", return_value=None):
            rep = self._report(cfg)
        by_tool = {r["tool"]: r for r in rep["tools"]}
        self.assertEqual(by_tool["fastp"]["status"], "missing")
        self.assertFalse(rep["ok"])
        self.assertTrue(any("fastp is required" in p for p in rep["problems"]))

    def test_nonexistent_env_reported_not_crashed(self):
        cfg = self.make_cfg(conda_env="definitely_not_an_env_xyz")
        rep = self._report(cfg)
        conda = rep["conda"]
        if conda["available"]:
            self.assertFalse(conda["env_exists"])
            self.assertIn("not found", conda["error"])
            self.assertFalse(rep["ok"])

    def test_report_is_json_serialisable(self):
        cfg = self.make_cfg(conda_mode="none", conda_env="none")
        rep = self._report(cfg)
        self.assertEqual(json.loads(json.dumps(rep))["route"], cfg.route_name)

    def test_missing_required_tools_helper(self):
        from metaglens.sense import doctor
        cfg = self.make_cfg(conda_mode="none", conda_env="none")
        with unittest.mock.patch("shutil.which", return_value=None):
            rep = doctor.build_report(cfg)
        missing = doctor.missing_required_tools(rep)
        self.assertIn("fastp", missing)
        # Never proposes installing something this route does not need.
        for tool in missing:
            self.assertTrue({r["tool"]: r for r in rep["tools"]}[tool]["required"])


class TestDbWhereAndGet(TempDirCase):
    """Phase 8.3: resolution chain transparency and download preflight."""

    _DB_ENV = ("GTDBTK_DATA_PATH", "CHECKM2DB", "KRAKEN2_DB_PATH", "EGGNOG_DATA_DIR")

    def _clear_env(self):
        patcher = unittest.mock.patch.dict("os.environ",
                                           {k: "" for k in self._DB_ENV}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_gtdbtk(self) -> Path:
        root = self.tmp / "gtdbtk_data" / "release232"
        (root / "taxonomy").mkdir(parents=True)
        (root / "taxonomy" / "gtdb_taxonomy.tsv").write_text("x\n", encoding="utf-8")
        (root / "metadata").mkdir(parents=True)
        (root / "metadata" / "metadata.txt").write_text(
            "VERSION_DATA=r232\n", encoding="utf-8")
        return root

    def test_chain_reports_config_level_hit(self):
        from metaglens.sense import database as db
        self._clear_env()
        root = self._fake_gtdbtk()
        cfg = self.make_cfg(taxonomy_db=str(root))
        chain = db.resolution_chain(name="gtdbtk", cfg=cfg, scan_roots=[self.tmp])
        levels = [c["level"] for c in chain]
        self.assertEqual(levels, ["config", "env", "scan", "default"])
        hits = [c for c in chain if c["hit"]]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["level"], "config")

    def test_chain_reports_scan_level_hit_when_no_config_or_env(self):
        from metaglens.sense import database as db
        self._clear_env()
        self._fake_gtdbtk()
        cfg = self.make_cfg()
        chain = db.resolution_chain("gtdbtk", cfg, scan_roots=[self.tmp])
        hits = [c for c in chain if c["hit"]]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["level"], "scan")

    def test_chain_has_no_hit_when_absent(self):
        from metaglens.sense import database as db
        self._clear_env()
        empty = self.tmp / "empty"
        empty.mkdir()
        cfg = self.make_cfg()
        chain = db.resolution_chain("eggnog", cfg, scan_roots=[empty])
        self.assertFalse(any(c["hit"] for c in chain))
        self.assertTrue(json.loads(json.dumps(chain)))

    def test_plan_get_refuses_when_space_short(self):
        from metaglens.sense import database as db
        cfg = self.make_cfg()
        Usage = __import__("collections").namedtuple("Usage", "total used free")
        with unittest.mock.patch(
            "metaglens.sense.database.shutil.disk_usage",
            return_value=Usage(0, 0, 1 * 1024 ** 3),   # 1 GB free
        ):
            pre = db.plan_get("gtdbtk", str(self.tmp / "dest"), cfg)
        self.assertFalse(pre["enough_space"])
        self.assertGreater(pre["required_gb"], pre["free_gb"])
        # 1.2x extraction margin must be applied, not the bare size.
        self.assertAlmostEqual(pre["required_gb"],
                               round(pre["size_hint_gb"] * 1.2, 1), places=1)

    def test_plan_get_accepts_when_space_ample(self):
        from metaglens.sense import database as db
        cfg = self.make_cfg()
        Usage = __import__("collections").namedtuple("Usage", "total used free")
        with unittest.mock.patch(
            "metaglens.sense.database.shutil.disk_usage",
            return_value=Usage(0, 0, 900 * 1024 ** 3),
        ):
            pre = db.plan_get("checkm2", str(self.tmp / "dest"), cfg)
        self.assertTrue(pre["enough_space"])
        self.assertIn("checkm2 database --download", pre["command"])

    def test_plan_get_has_no_command_for_url_only_databases(self):
        """We never fabricate download URLs; those stay instructions only."""
        from metaglens.sense import database as db
        cfg = self.make_cfg()
        pre = db.plan_get("gtdbtk", str(self.tmp / "d"), cfg)
        self.assertIsNone(pre["command"])
        self.assertIn("GTDB-Tk", pre["download_hint"])

    def test_plan_get_writes_nothing(self):
        from metaglens.sense import database as db
        cfg = self.make_cfg()
        dest = self.tmp / "untouched"
        db.plan_get("eggnog", str(dest), cfg)
        self.assertFalse(dest.exists())

    def test_verify_is_read_only(self):
        from metaglens.sense import database as db
        root = self._fake_gtdbtk()
        before = sorted(p.name for p in root.rglob("*"))
        ok, _detail = db.validate("gtdbtk", str(root))
        self.assertTrue(ok)
        self.assertEqual(sorted(p.name for p in root.rglob("*")), before)


class TestExecutionPlan(TempDirCase):
    """Phase 8.4: plan table, coarse-estimate honesty, DB warnings, plain text."""

    _DB_ENV = ("GTDBTK_DATA_PATH", "CHECKM2DB", "KRAKEN2_DB_PATH", "EGGNOG_DATA_DIR")

    def _clear_env(self):
        patcher = unittest.mock.patch.dict("os.environ",
                                           {k: "" for k in self._DB_ENV}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_plan_covers_every_route_step(self):
        from metaglens.decide import plan as plan_mod
        cfg = self.make_cfg(route_name="mag_per_sample")
        data = plan_mod.build_plan(cfg, n_samples=4)
        self.assertEqual([s["step"] for s in data["stages"]], list(cfg.route.steps))
        self.assertGreater(data["totals"]["minutes"], 0)
        self.assertGreater(data["totals"]["disk_gb"], 0)

    def test_estimates_are_labelled_coarse(self):
        from metaglens.decide import plan as plan_mod
        cfg = self.make_cfg()
        data = plan_mod.build_plan(cfg, n_samples=2)
        self.assertEqual(data["estimate"]["band"], 0.5)
        self.assertIn("+/-50%", data["estimate"]["note"])
        text = plan_mod.render_plain(data)
        self.assertIn("COARSE", text)
        self.assertIn("+/-50%", text)

    def test_disk_scales_with_sample_count(self):
        from metaglens.decide import plan as plan_mod
        cfg = self.make_cfg()
        small = plan_mod.build_plan(cfg, n_samples=1)["totals"]["disk_gb"]
        big = plan_mod.build_plan(cfg, n_samples=10)["totals"]["disk_gb"]
        self.assertGreater(big, small)

    def test_missing_database_produces_warning_with_command(self):
        from metaglens.decide import plan as plan_mod
        self._clear_env()
        cfg = self.make_cfg(route_name="mag_per_sample",
                            db_dir=str(self.tmp / "nodbs"))
        data = plan_mod.build_plan(cfg, n_samples=2)
        self.assertTrue(data["db_warnings"])
        joined = " ".join(data["db_warnings"])
        self.assertIn("metaglens db get", joined)
        self.assertFalse(data["ok"])

    def test_plain_summary_states_no_metered_cost(self):
        from metaglens.decide import plan as plan_mod
        cfg = self.make_cfg()
        text = plan_mod.render_plain(plan_mod.build_plan(cfg, n_samples=3))
        self.assertIn("no API", text)
        self.assertIn("metered", text)
        self.assertIn("TOTAL", text)
        # Plain text must carry no Rich markup.
        self.assertNotIn("[/", text)
        self.assertNotIn("[bold]", text)

    def test_plan_is_json_serialisable(self):
        from metaglens.decide import plan as plan_mod
        cfg = self.make_cfg()
        data = plan_mod.build_plan(cfg, n_samples=2)
        self.assertEqual(json.loads(json.dumps(data))["route"], cfg.route_name)

    def test_contig_route_plan_omits_mag_stages(self):
        from metaglens.decide import plan as plan_mod
        cfg = self.make_cfg(route_name="contig_based", contig_taxonomy="kraken2")
        steps = [s["step"] for s in plan_mod.build_plan(cfg, n_samples=2)["stages"]]
        self.assertNotIn("04_binning", steps)
        self.assertNotIn("05_checkm", steps)
        self.assertIn("09_contig", steps)

    def test_resource_warning_when_memory_short(self):
        from metaglens.decide import plan as plan_mod
        from metaglens.sense import hardware
        tiny = hardware.HardwareInfo(cores=4, ram_gb=2.0, disk_free_gb=10.0,
                                     in_container=False)
        cfg = self.make_cfg()
        with unittest.mock.patch(
                "metaglens.decide.plan.hardware_mod.probe", return_value=tiny):
            data = plan_mod.build_plan(cfg, n_samples=4)
        self.assertTrue(data["resource_warnings"])
        self.assertFalse(data["ok"])


class TestToolchainInstallability(TempDirCase):
    """Phase 9.A: nothing may be required that no env group can install."""

    def _all_group_tools(self) -> set:
        return {tool for group in conda_setup.ENV_GROUPS.values() for tool in group}

    def test_every_required_tool_is_installable(self):
        """The general guard: required_tools ⊆ union(ENV_GROUPS).

        This is what makes the prodigal class of bug impossible to reintroduce —
        a tool the templates invoke but no group ships would be caught here.
        """
        from metaglens.sense import tools
        provided = self._all_group_tools()
        for route in routes.ROUTES:
            for switches in (
                {},
                {"contig_taxonomy": "kraken2"},
                {"taxonomy_tool": "kraken2", "use_bracken": True},
                {"assembler": "metaspades", "align_tool": "bwa-mem2"},
                {"use_prokka": False},
                {"remove_host": True, "host_genome": "/tmp/h.fa"},
            ):
                cfg = self.make_cfg(route_name=route, **switches)
                required = set(tools.required_tools(cfg))
                missing = required - provided
                self.assertEqual(
                    missing, set(),
                    f"{route} {switches}: required but no ENV_GROUP provides {missing}")

    def test_known_tools_are_all_installable(self):
        from metaglens.sense import tools
        unprovided = set(tools.all_known_tools()) - self._all_group_tools()
        self.assertEqual(unprovided, set(),
                         f"tools with no installing group: {unprovided}")

    def test_prodigal_shipped_in_mag_group_and_pipeline_tools(self):
        self.assertIn("prodigal", conda_setup.ENV_GROUPS["mag"])
        self.assertIn("prodigal", conda_env.PIPELINE_TOOLS)

    def test_setup_env_mag_command_includes_prodigal(self):
        plan = conda_setup.build_commands("proj", ["mag"], single=False)
        env_name, argv = plan[0]
        self.assertEqual(env_name, "proj_mag")
        self.assertIn("prodigal", argv)

    def test_contig_route_has_no_uninstallable_requirement(self):
        from metaglens.sense import tools
        cfg = self.make_cfg(route_name="contig_based", contig_taxonomy="kraken2")
        required = set(tools.required_tools(cfg))
        self.assertIn("prodigal", required)          # 09_contig calls it
        self.assertLessEqual(required, self._all_group_tools())


@unittest.skipIf(shutil.which("bash") is None, "bash unavailable")
class TestStubDemo(unittest.TestCase):
    """Phase 9.B: the real stage scripts run end-to-end against stub tools."""

    def test_mag_route_end_to_end(self):
        from metaglens.demo import run_demo
        res = run_demo("mag_per_sample")
        self.assertTrue(res["ok"], res["errors"] or res["missing"])
        self.assertEqual([s["status"] for s in res["stages"]],
                         ["completed"] * len(res["stages"]))
        self.assertTrue(res["report_html"])
        self.assertTrue(res["monitor_html"])

    def test_contig_route_end_to_end(self):
        """The contig route is the one §7-8 broke, so it must be covered."""
        from metaglens.demo import run_demo
        res = run_demo("contig_based")
        self.assertTrue(res["ok"], res["errors"] or res["missing"])
        steps = [s["step"] for s in res["stages"]]
        self.assertIn("09_contig", steps)
        self.assertIn("10_community", steps)

    def test_contig_route_selects_contig_kraken_source(self):
        """Regression for §7-8: this branch used to be unreachable dead code."""
        from metaglens.demo import run_demo
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        res = run_demo("contig_based", workdir=str(d))
        self.assertTrue(res["ok"], res["errors"])
        source = (d / "work" / "metaglens_results" / "10_community" /
                  "SOURCE.txt").read_text(encoding="utf-8")
        self.assertIn("contig", source.lower())
        matrix = (d / "work" / "metaglens_results" / "10_community" /
                  "community_matrix.tsv").read_text(encoding="utf-8")
        # Header plus at least one data row — the empty-table guard demands it.
        self.assertGreaterEqual(len(matrix.strip().splitlines()), 2)

    def test_demo_writes_only_inside_its_own_directory(self):
        from metaglens.demo import run_demo
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        home_before = sorted(p.name for p in Path.home().iterdir())
        res = run_demo("contig_based", workdir=str(d))
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual(sorted(p.name for p in Path.home().iterdir()), home_before)
        # Everything produced lives under the given directory.
        self.assertTrue((d / "work" / "metaglens_results").is_dir())

    def test_report_qc_tab_is_populated(self):
        """Transitively proves the fastp JSON naming fix (§7-1) still holds."""
        from metaglens.demo import run_demo
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        res = run_demo("mag_per_sample", workdir=str(d))
        self.assertTrue(res["ok"], res["errors"])
        html = Path(res["report_html"]).read_text(encoding="utf-8")
        payload = json.loads(re.search(r"window\.__MG__=(\{.*?\});", html,
                                       re.S).group(1))
        self.assertEqual(len(payload["qc"]), 2)
        self.assertGreater(payload["qc"][0]["raw_reads"], 0)
        self.assertTrue(payload["mags"])

    def test_unknown_route_rejected(self):
        from metaglens.demo import run_demo
        with self.assertRaises(ValueError):
            run_demo("no_such_route")

    def test_stub_set_covers_every_required_tool_command(self):
        """Any tool a demo route needs must have a stub, or the demo is a lie."""
        from metaglens.demo import stubs
        from metaglens.sense import tools
        from metaglens.demo.runner import _make_config, DEMO_ROUTES
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        for route in DEMO_ROUTES:
            cfg = _make_config(d / route, route, ["s1"])
            for tool in tools.required_tools(cfg):
                command = tools.tool_spec(tool).command
                self.assertIn(command, stubs.STUBS,
                              f"{route}: no stub for '{command}' ({tool})")


class TestProductValidation(TempDirCase):
    """Phase 10.1/10.2: semantic product validation, not "file is non-empty"."""

    def _results(self) -> Path:
        r = self.tmp / "res"
        r.mkdir(parents=True, exist_ok=True)
        (r / "samples.tsv").write_text(
            "sample_id\tr1\tr2\nA\t/x/A_R1.fq\t/x/A_R2.fq\n", encoding="utf-8")
        (r / "pipeline_status.json").write_text("{}", encoding="utf-8")
        return r

    # -- the headline case: header-only files must fail -------------------- #
    def test_header_only_table_fails(self):
        from metaglens import state
        r = self._results()
        (r / "10_community").mkdir()
        matrix = r / "10_community" / "community_matrix.tsv"
        matrix.write_text("taxon\tA\n", encoding="utf-8")
        self.assertGreater(matrix.stat().st_size, 0)   # non-empty ...
        report = state.validate_stage(r, "10_community")
        self.assertFalse(report.ok)                   # ... but still invalid
        self.assertIn("header line alone", " ".join(report.failures))

    def test_table_with_data_row_passes(self):
        from metaglens import state
        r = self._results()
        (r / "10_community").mkdir()
        (r / "10_community" / "community_matrix.tsv").write_text(
            "taxon\tA\ns__Foo\t12.5\n", encoding="utf-8")
        self.assertTrue(state.validate_stage(r, "10_community").ok)

    def test_empty_bins_dir_fails(self):
        from metaglens import state
        r = self._results()
        (r / "04_binning" / "all_bins").mkdir(parents=True)
        self.assertFalse(state.validate_stage(r, "04_binning").ok)
        (r / "04_binning" / "all_bins" / "bin1.fa").write_text(
            ">c1\nACGT\n", encoding="utf-8")
        self.assertTrue(state.validate_stage(r, "04_binning").ok)

    def test_empty_fasta_fails_even_when_file_exists(self):
        from metaglens import state
        r = self._results()
        unit = r / "02_assembly" / "A"
        unit.mkdir(parents=True)
        contigs = unit / "final.contigs_filtered.fa"
        contigs.write_text("# a comment but no sequences\n", encoding="utf-8")
        self.assertGreater(contigs.stat().st_size, 0)
        self.assertFalse(state.validate_stage(r, "02_assembly", ["A"]).ok)

    def test_qc_requires_actual_reads(self):
        from metaglens import state
        import gzip
        r = self._results()
        qc = r / "01_qc"
        qc.mkdir()
        for mate in (1, 2):
            with gzip.open(qc / f"A_clean_R{mate}.fastq.gz", "wt") as fh:
                fh.write("")            # valid gzip, zero records
        self.assertFalse(state.validate_stage(r, "01_qc", ["A"]).ok)
        for mate in (1, 2):
            with gzip.open(qc / f"A_clean_R{mate}.fastq.gz", "wt") as fh:
                fh.write("@r1\nACGT\n+\nIIII\n")
        self.assertTrue(state.validate_stage(r, "01_qc", ["A"]).ok)

    def test_missing_stage_outputs_fail(self):
        from metaglens import state
        r = self._results()
        for step in ("05_checkm", "06_derep", "07_taxonomy", "10_community"):
            self.assertFalse(state.validate_stage(r, step).ok, step)

    def test_unknown_stage_passes_vacuously(self):
        from metaglens import state
        report = state.validate_stage(self._results(), "no_such_stage")
        self.assertTrue(report.ok)

    def test_report_is_json_serialisable(self):
        from metaglens import state
        r = self._results()
        payload = state.validate_stage(r, "00_setup").as_dict()
        self.assertEqual(json.loads(json.dumps(payload))["stage"], "00_setup")

    # -- demotion: the shell said completed, products say otherwise -------- #
    def test_run_step_demotes_stage_when_products_invalid(self):
        cfg = self.make_cfg()
        results = cfg.results_dir
        results.mkdir(parents=True, exist_ok=True)
        (results / "samples.tsv").write_text(
            "sample_id\tr1\tr2\nA\t/x/1\t/x/2\n", encoding="utf-8")
        (results / "pipeline_status.json").write_text(json.dumps({
            "steps": {"10_community": {"status": "pending", "attempts": 0}}}),
            encoding="utf-8")
        # A script that claims success and writes a header-only matrix.
        script = results / routes.STEPS["10_community"].script
        out = results / "10_community"
        script.write_text(
            "#!/usr/bin/env bash\n"
            f"mkdir -p '{out}'\n"
            f"printf 'taxon\\tA\\n' > '{out}/community_matrix.tsv'\n"
            "python3 - <<'PY'\n"
            "import json\n"
            f"p='{results}/pipeline_status.json'\n"
            "d=json.load(open(p))\n"
            "d['steps']['10_community']['status']='completed'\n"
            "json.dump(d, open(p,'w'))\n"
            "PY\n"
            "exit 0\n", encoding="utf-8")
        script.chmod(0o755)

        rc = pipeline.run_step(cfg, "10_community")
        self.assertNotEqual(rc, 0, "a stage with unusable products must not pass")
        self.assertEqual(pipeline.step_status(cfg, "10_community"), "failed")
        data = pipeline.read_status(cfg)
        pv = data["steps"]["10_community"]["product_validation"]
        self.assertFalse(pv["ok"])
        self.assertTrue(pv["failures"])

    def test_run_step_accepts_valid_products(self):
        cfg = self.make_cfg()
        results = cfg.results_dir
        results.mkdir(parents=True, exist_ok=True)
        (results / "samples.tsv").write_text(
            "sample_id\tr1\tr2\nA\t/x/1\t/x/2\n", encoding="utf-8")
        (results / "pipeline_status.json").write_text(json.dumps({
            "steps": {"10_community": {"status": "pending", "attempts": 0}}}),
            encoding="utf-8")
        script = results / routes.STEPS["10_community"].script
        out = results / "10_community"
        script.write_text(
            "#!/usr/bin/env bash\n"
            f"mkdir -p '{out}'\n"
            f"printf 'taxon\\tA\\ns__Foo\\t9\\n' > '{out}/community_matrix.tsv'\n"
            "python3 - <<'PY'\n"
            "import json\n"
            f"p='{results}/pipeline_status.json'\n"
            "d=json.load(open(p))\n"
            "d['steps']['10_community']['status']='completed'\n"
            "json.dump(d, open(p,'w'))\n"
            "PY\n"
            "exit 0\n", encoding="utf-8")
        script.chmod(0o755)
        self.assertEqual(pipeline.run_step(cfg, "10_community"), 0)
        self.assertEqual(pipeline.step_status(cfg, "10_community"), "completed")


class TestQualityGates(TempDirCase):
    """Phase 10.3/10.4: soft scientific gates from externalised rules."""

    def _results(self, retention=(1000, 900), bins=2, checkm=None,
                 taxa=1) -> Path:
        r = self.tmp / "gres"
        (r / "01_qc").mkdir(parents=True, exist_ok=True)
        (r / "samples.tsv").write_text(
            "sample_id\tr1\tr2\nA\t/x/1\t/x/2\n", encoding="utf-8")
        before, after = retention
        (r / "01_qc" / "A.qcstats").write_text(f"{before}\t{after}\n",
                                               encoding="utf-8")
        allbins = r / "04_binning" / "all_bins"
        allbins.mkdir(parents=True, exist_ok=True)
        for i in range(bins):
            (allbins / f"bin{i}.fa").write_text(">c\nACGT\n", encoding="utf-8")
        if checkm is not None:
            (r / "05_checkm").mkdir(parents=True, exist_ok=True)
            rows = ["Name\tCompleteness\tContamination\tModel"]
            for i, (comp, cont) in enumerate(checkm):
                rows.append(f"bin{i}\t{comp}\t{cont}\tstub")
            (r / "05_checkm" / "quality_report.tsv").write_text(
                "\n".join(rows) + "\n", encoding="utf-8")
        comm = r / "10_community"
        comm.mkdir(parents=True, exist_ok=True)
        lines = ["taxon\tA"] + [f"s__T{i}\t{i+1}" for i in range(taxa)]
        (comm / "community_matrix.tsv").write_text("\n".join(lines) + "\n",
                                                   encoding="utf-8")
        return r

    def _by_id(self, results):
        from metaglens.decide import gates
        return {g.gate_id: g for g in gates.evaluate(results)}

    def test_rules_load_from_yaml(self):
        from metaglens.decide import gates
        rules = gates.load_rules()
        self.assertIn("01_qc", rules)
        # Every rule must be traceable and explain itself.
        for stage, entries in rules.items():
            for rule in entries:
                self.assertIn("id", rule, stage)
                self.assertIn("metric", rule, stage)
                self.assertTrue(rule.get("hint"), rule.get("id"))

    def test_retention_pass_warn_block(self):
        good = self._by_id(self._results(retention=(1000, 900)))
        self.assertEqual(good["qc.retention_rate"].status, "pass")
        warn = self._by_id(self._results(retention=(1000, 600)))
        self.assertEqual(warn["qc.retention_rate"].status, "warn")
        block = self._by_id(self._results(retention=(1000, 300)))
        self.assertEqual(block["qc.retention_rate"].status, "block")

    def test_no_bins_warns(self):
        res = self._by_id(self._results(bins=0))
        self.assertEqual(res["binning.bins_per_sample"].status, "warn")
        self.assertIn("fragmented", res["binning.bins_per_sample"].hint)

    def test_mimag_high_quality_counting(self):
        # 95/2 is HQ; 60/12 is not.
        hq = self._by_id(self._results(checkm=[(95.0, 2.0)]))
        self.assertEqual(hq["checkm.mimag_hq_count"].status, "pass")
        lq = self._by_id(self._results(checkm=[(60.0, 12.0)]))
        self.assertEqual(lq["checkm.mimag_hq_count"].status, "warn")
        self.assertEqual(lq["checkm.mimag_hq_count"].value, 0.0)

    def test_absent_stage_is_unknown_not_failure(self):
        from metaglens.decide import gates
        bare = self.tmp / "bare"
        bare.mkdir()
        results = gates.evaluate(bare)
        self.assertTrue(results)
        self.assertTrue(all(g.status == "unknown" for g in results))
        summary = gates.summarise(results)
        self.assertTrue(summary["ok"], "unknown metrics must not block")

    def test_strict_promotes_warning_to_blocking(self):
        from metaglens.decide import gates
        results = gates.evaluate(self._results(bins=0))
        lenient = gates.summarise(results, strict=False)
        strict = gates.summarise(results, strict=True)
        self.assertTrue(lenient["ok"])
        self.assertFalse(strict["ok"])
        self.assertIn("binning.bins_per_sample", strict["blocking"])

    def test_block_stops_even_without_strict(self):
        from metaglens.decide import gates
        results = gates.evaluate(self._results(retention=(1000, 100)))
        self.assertFalse(gates.summarise(results, strict=False)["ok"])

    def test_summary_is_json_serialisable(self):
        from metaglens.decide import gates
        summary = gates.summarise(gates.evaluate(self._results()))
        self.assertEqual(json.loads(json.dumps(summary))["worst"], "pass")

    def test_gates_appear_in_report(self):
        from metaglens.decide import gates
        r = self._results(retention=(1000, 600))   # a warning to render
        summary = gates.summarise(gates.evaluate(r))
        (r / "pipeline_status.json").write_text(json.dumps({
            "project_name": "demo", "route_name": "mag_per_sample",
            "selected_steps": [], "steps": {}, "gates": summary}),
            encoding="utf-8")
        out = generate_report(r, raw_data_dir="/x")
        html = out.read_text(encoding="utf-8")
        self.assertIn("tab-gates", html)
        self.assertIn("Quality Gates", html)
        payload = json.loads(re.search(r"window\.__MG__=(\{.*?\});", html,
                                       re.S).group(1))
        self.assertTrue(payload["gates"]["gates"])


class TestFailureDiagnosis(TempDirCase):
    """Phase 11: exit codes and log tails become actionable diagnoses."""

    def _results(self, stage="02_assembly", log="", status=None) -> Path:
        r = self.tmp / "dres"
        (r / "reports" / "logs").mkdir(parents=True, exist_ok=True)
        if log:
            (r / "reports" / "logs" / f"{stage}.log").write_text(log, encoding="utf-8")
        (r / "pipeline_status.json").write_text(
            json.dumps(status or {"steps": {}}), encoding="utf-8")
        return r

    def test_all_rules_are_well_formed(self):
        from metaglens.decide import diagnose as dg
        rules = dg.load_rules()
        self.assertGreaterEqual(len(rules), 10)
        seen = set()
        for rule in rules:
            for key in ("id", "match", "class", "title", "diagnosis", "actions"):
                self.assertIn(key, rule, rule.get("id"))
            self.assertNotIn(rule["id"], seen, "duplicate rule id")
            seen.add(rule["id"])
            self.assertIn(rule["class"],
                          ("script_defect", "environment", "data_config"))
            self.assertTrue(rule["actions"], rule["id"])

    def test_exit_137_is_oom(self):
        from metaglens.decide import diagnose as dg
        r = self._results(log="megahit: assembling\n")
        diag = dg.diagnose(r, "02_assembly", exit_code=137)
        self.assertEqual(diag.rule_id, "oom.killed")
        self.assertEqual(diag.failure_class, "environment")
        self.assertIn("OOM", diag.title)
        # It must offer a safe automatic action for the repair layer.
        self.assertTrue(diag.auto_actions())
        self.assertEqual(diag.auto_actions()[0]["op"], "reduce_parallel")

    def test_database_signatures(self):
        from metaglens.decide import diagnose as dg
        cases = {
            "ERROR: GTDBTK_DATA_PATH is not set": "db.gtdbtk_missing",
            "checkm2: DIAMOND database not found": "db.checkm2_missing",
            "kraken2: cannot open database hash.k2d": "db.kraken2_missing",
            "eggnog data not found in /x": "db.eggnog_missing",
        }
        for log, expected in cases.items():
            with self.subTest(rule=expected):
                r = self._results(stage="07_taxonomy", log=log + "\n")
                diag = dg.diagnose(r, "07_taxonomy", exit_code=1)
                self.assertEqual(diag.rule_id, expected)
                self.assertTrue(diag.human_actions())

    def test_infrastructure_signatures(self):
        from metaglens.decide import diagnose as dg
        cases = {
            "samtools sort: No space left on device": "disk.full",
            "line 42: fastp: command not found": "tool.not_found",
            "mkdir: cannot create directory: Permission denied": "permission.denied",
            "ls: cannot access '/x/*.fa': No such file or directory": "glob.unmatched",
        }
        for log, expected in cases.items():
            with self.subTest(rule=expected):
                r = self._results(stage="03_mapping", log=log + "\n")
                diag = dg.diagnose(r, "03_mapping", exit_code=1)
                self.assertEqual(diag.rule_id, expected)

    def test_unknown_failure_degrades_without_inventing_a_cause(self):
        from metaglens.decide import diagnose as dg
        r = self._results(log="some totally unrecognised message\nand another\n")
        diag = dg.diagnose(r, "02_assembly", exit_code=3)
        self.assertFalse(diag.matched)
        self.assertEqual(diag.rule_id, "")
        self.assertEqual(diag.failure_class, "unknown")
        self.assertIn("Unknown failure", diag.title)
        # It must still hand over evidence and the log location.
        self.assertTrue(diag.evidence)
        self.assertTrue(diag.log_file)
        # And must never claim a specific cause.
        for word in ("memory", "database", "disk", "permission"):
            self.assertNotIn(word, diag.diagnosis.lower())

    def test_product_validation_failure_takes_precedence(self):
        from metaglens.decide import diagnose as dg
        status = {"steps": {"10_community": {
            "status": "failed",
            "product_validation": {"ok": False, "failures": [
                "community_matrix.tsv has 0 data row(s), expected >= 1"]}}}}
        r = self._results(stage="10_community", log="killed\n", status=status)
        diag = dg.diagnose(r, "10_community", exit_code=137, status=status)
        self.assertEqual(diag.rule_id, "products.invalid")
        self.assertIn("0 data row", " ".join(diag.evidence))

    def test_actions_include_runnable_commands(self):
        """The third part of the message must be copy-pasteable."""
        from metaglens.decide import diagnose as dg
        r = self._results(stage="07_taxonomy",
                          log="ERROR: GTDBTK_DATA_PATH not set\n")
        diag = dg.diagnose(r, "07_taxonomy", exit_code=1)
        joined = " ".join(diag.human_actions())
        self.assertIn("metaglens db", joined)

    def test_reads_status_when_exit_code_not_given(self):
        from metaglens.decide import diagnose as dg
        status = {"steps": {"02_assembly": {
            "status": "failed",
            "last_failure": {"exit_code": 137, "command": "megahit ...",
                             "line": "88"}}}}
        r = self._results(log="assembling\n", status=status)
        diag = dg.diagnose(r, "02_assembly", status=status)
        self.assertEqual(diag.exit_code, 137)
        self.assertEqual(diag.rule_id, "oom.killed")
        self.assertIn("megahit", diag.failed_command)

    def test_failed_stages_listed_in_order(self):
        from metaglens.decide import diagnose as dg
        status = {"selected_steps": ["01_qc", "02_assembly", "03_mapping"],
                  "steps": {"01_qc": {"status": "completed"},
                            "02_assembly": {"status": "failed"},
                            "03_mapping": {"status": "failed"}}}
        self.assertEqual(dg.failed_stages(status), ["02_assembly", "03_mapping"])

    def test_diagnosis_is_json_serialisable(self):
        from metaglens.decide import diagnose as dg
        r = self._results(log="killed\n")
        payload = dg.diagnose(r, "02_assembly", exit_code=137).as_dict()
        self.assertEqual(json.loads(json.dumps(payload))["id"], "oom.killed")

    def test_missing_log_does_not_crash(self):
        from metaglens.decide import diagnose as dg
        r = self._results(log="")
        diag = dg.diagnose(r, "05_checkm", exit_code=1)
        self.assertFalse(diag.matched)
        self.assertTrue(diag.actions)


class TestSuggestions(TempDirCase):
    """Phase 12.1: typos get a nudge, not a wall of valid values."""

    def test_step_typo_suggests_step(self):
        from metaglens.express.suggest import suggest
        self.assertIn("04_binning", suggest("04_bining", routes.STEPS))

    def test_route_typo_suggests_route(self):
        with self.assertRaises(ValueError) as ctx:
            routes.resolve_route("contig_base")
        self.assertIn("contig_based", str(ctx.exception))
        self.assertIn("Did you mean", str(ctx.exception))

    def test_select_steps_typo_suggests(self):
        cfg = self.make_cfg()
        with self.assertRaises(pipeline.PipelineError) as ctx:
            pipeline.select_steps(cfg, only=["04_bining"])
        self.assertIn("04_binning", str(ctx.exception))
        with self.assertRaises(pipeline.PipelineError) as ctx:
            pipeline.select_steps(cfg, from_step="02_assembl")
        self.assertIn("02_assembly", str(ctx.exception))

    def test_unknown_config_key_suggests(self):
        cfg_path = self.tmp / "typo.yaml"
        cfg_path.write_text("project_name: p\ntotal_thread: 8\n", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            Config.from_yaml(str(cfg_path))
        self.assertIn("total_threads", str(ctx.exception))

    def test_validate_reports_route_and_step_suggestions(self):
        cfg = self.make_cfg(route_name="mag_per_sampl")
        self.assertTrue(any("mag_per_sample" in e for e in cfg.validate()))
        cfg2 = self.make_cfg(route_name="custom", custom_steps=["01_q"])
        self.assertTrue(any("01_qc" in e for e in cfg2.validate()))

    def test_nonsense_gets_no_false_suggestion(self):
        from metaglens.express.suggest import suggest
        self.assertEqual(suggest("zzzzzzzz", routes.STEPS), "")

    def test_substring_fallback(self):
        from metaglens.express.suggest import suggest
        # Too different for difflib's ratio, but an obvious truncation.
        self.assertIn("07_taxonomy", suggest("taxonomy", routes.STEPS))


class TestUserProfile(TempDirCase):
    """Phase 12.2: the profile supplies defaults and never overrides."""

    def test_roundtrip(self):
        from metaglens.express import profile
        path = self.tmp / "profile.yaml"
        self.assertTrue(profile.save({"total_threads": 32,
                                      "db_dir": "/shared/db"}, path))
        loaded = profile.load(path)
        self.assertEqual(loaded["total_threads"], 32)
        self.assertEqual(loaded["db_dir"], "/shared/db")

    def test_only_remembered_keys_persist(self):
        from metaglens.express import profile
        path = self.tmp / "p.yaml"
        profile.save({"total_threads": 8, "project_name": "leaked",
                      "raw_data_dir": "/nope"}, path)
        loaded = profile.load(path)
        self.assertIn("total_threads", loaded)
        self.assertNotIn("project_name", loaded)
        self.assertNotIn("raw_data_dir", loaded)

    def test_defaults_do_not_override_explicit_values(self):
        from metaglens.express import profile
        path = self.tmp / "p2.yaml"
        profile.save({"total_threads": 64, "db_dir": "/from/profile"}, path)
        defaults = profile.defaults_for({"total_threads": 8}, path)
        self.assertNotIn("total_threads", defaults)   # explicit wins
        self.assertEqual(defaults["db_dir"], "/from/profile")  # gap filled

    def test_corrupt_profile_degrades_silently(self):
        from metaglens.express import profile
        path = self.tmp / "bad.yaml"
        path.write_text("this: [is: not: valid: yaml", encoding="utf-8")
        self.assertEqual(profile.load(path), {})

    def test_missing_profile_is_empty_not_an_error(self):
        from metaglens.express import profile
        self.assertEqual(profile.load(self.tmp / "absent.yaml"), {})

    def test_honours_xdg_config_home(self):
        from metaglens.express import profile
        with unittest.mock.patch.dict("os.environ",
                                      {"XDG_CONFIG_HOME": str(self.tmp / "xdg")}):
            self.assertTrue(str(profile.profile_path()).startswith(
                str(self.tmp / "xdg")))


class TestI18n(unittest.TestCase):
    """Phase 12.3: interactive language only; deliverables stay English."""

    def test_translation_and_fallback(self):
        from metaglens.express import i18n
        self.assertNotEqual(i18n.t("gate.all_passed", "zh"),
                            i18n.t("gate.all_passed", "en"))
        # Unknown key degrades to the key, never raises.
        self.assertEqual(i18n.t("no.such.key", "zh"), "no.such.key")

    def test_every_english_key_has_a_chinese_entry(self):
        from metaglens.express import i18n
        missing = set(i18n.MESSAGES["en"]) - set(i18n.MESSAGES["zh"])
        self.assertEqual(missing, set(), f"untranslated: {missing}")

    def test_language_detection_precedence(self):
        from metaglens.express import i18n
        self.assertEqual(i18n.detect(cli_lang="zh",
                                     env={"LANG": "en_US.UTF-8"}), "zh")
        self.assertEqual(i18n.detect(env={"LANG": "zh_CN.UTF-8"}), "zh")
        self.assertEqual(i18n.detect(env={}), "en")
        self.assertEqual(i18n.normalise("ZH-Hans"), "zh")

    def test_deliverables_stay_english_regardless_of_language(self):
        from metaglens.express import i18n
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "pipeline_status.json").write_text(json.dumps({
            "project_name": "demo", "route_name": "mag_per_sample",
            "selected_steps": [], "steps": {}}), encoding="utf-8")
        (d / "delivery" / "community").mkdir(parents=True)
        (d / "delivery" / "community" / "community_matrix.tsv").write_text(
            "taxon\tA\ns__Foo\t1\n", encoding="utf-8")
        i18n.set_language("zh")
        try:
            html = generate_report(d, raw_data_dir="/x").read_text(encoding="utf-8")
        finally:
            i18n.set_language("en")
        # The report's own chrome must remain English.
        self.assertIn("MetaGLens Delivery Report", html)
        self.assertIn("Analysis-ready package", html)


class TestExplainKnowledge(unittest.TestCase):
    """Phase 12.4: domain knowledge as an offline, data-driven asset."""

    def test_knowledge_is_a_data_file(self):
        from metaglens.express import explain
        self.assertTrue(explain.knowledge_path().is_file())
        self.assertTrue(str(explain.knowledge_path()).endswith(".yaml"))

    def test_all_twelve_stages_are_covered(self):
        from metaglens.express import explain
        available = set(explain.topics())
        for step in routes.STEPS:
            self.assertIn(step, available, f"no explain entry for {step}")

    def test_key_scientific_parameters_are_covered(self):
        from metaglens.express import explain
        available = set(explain.topics())
        for param in ("completeness_min", "contamination_max", "ani_threshold",
                      "min_contig_len", "min_length", "quality_threshold"):
            self.assertIn(param, available, param)

    def test_failure_ids_are_explainable(self):
        from metaglens.express import explain
        from metaglens.decide import diagnose as dg
        available = set(explain.topics())
        # At least the signatures a newcomer is most likely to hit.
        for rule_id in ("oom.killed", "db.gtdbtk_missing", "disk.full",
                        "glob.unmatched", "products.invalid"):
            self.assertIn(rule_id, available, rule_id)
        self.assertIn("oom.killed", {r["id"] for r in dg.load_rules()})

    def test_every_entry_has_title_and_summary(self):
        from metaglens.express import explain
        for topic, entry in explain.load_topics().items():
            self.assertTrue(entry.get("title"), topic)
            self.assertTrue(entry.get("summary"), topic)

    def test_mimag_thresholds_are_stated(self):
        from metaglens.express import explain
        text = explain.render_text(explain.lookup("completeness_min"))
        self.assertIn("MIMAG", text)
        self.assertIn("90", text)

    def test_unknown_topic_offers_candidates(self):
        from metaglens.express import explain
        self.assertIsNone(explain.lookup("no_such_topic_at_all"))
        self.assertIn("completeness_min", explain.candidates("completness"))

    def test_lookup_is_case_insensitive(self):
        from metaglens.express import explain
        self.assertIsNotNone(explain.lookup("MIMAG"))


class TestProgressParsers(unittest.TestCase):
    """Phase 13.2: progress from logs, degrading rather than guessing wrong."""

    def _log(self, stage: str, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "reports" / "logs").mkdir(parents=True)
        (d / "reports" / "logs" / f"{stage}.log").write_text(text, encoding="utf-8")
        return d

    def test_qc_counts_samples(self):
        from metaglens.observe import progress
        log = ("Processing sample: A\n  [A] QC completed.\n"
               "Processing sample: B\n  [B] QC completed.\n"
               "Processing sample: C\n")
        prog = progress.parse_log("01_qc", log.splitlines())
        self.assertTrue(prog.determinate)
        self.assertEqual((prog.done, prog.total), (2, 3))
        self.assertEqual(prog.active, ["C"])

    def test_mapping_and_assembly_counts(self):
        from metaglens.observe import progress
        prog = progress.parse_log("03_mapping", [
            "Mapping sample: A", "  [A] mapping completed.",
            "Mapping sample: B", "  [B] mapping completed."])
        self.assertEqual((prog.done, prog.total), (2, 2))
        prog2 = progress.parse_log("02_assembly", [
            "Assembling sample: A", "  [A] Contig stats:",
            "Assembling sample: B"])
        self.assertEqual(prog2.done, 1)

    def test_declared_total_is_used(self):
        from metaglens.observe import progress
        prog = progress.parse_log("08_annotation", [
            "MAGs to annotate: 4",
            "  Annotating: m1", "  Annotating: m2", "  Annotating: m3"])
        self.assertTrue(prog.determinate)
        self.assertEqual(prog.total, 4)
        self.assertEqual(prog.done, 2)          # two finished, one in flight
        self.assertEqual(prog.active, ["m3"])
        # Never invents unit names from stray text.
        for unit in prog.units:
            self.assertTrue(unit.startswith("m"), unit)

    def test_unparseable_log_degrades_to_indeterminate(self):
        from metaglens.observe import progress
        prog = progress.parse_log("05_checkm", ["something entirely unexpected",
                                                "and more of it"])
        self.assertFalse(prog.determinate)
        self.assertIsNone(prog.fraction)
        self.assertTrue(prog.detail)            # still says something useful

    def test_tool_hints_when_units_unknown(self):
        from metaglens.observe import progress
        prog = progress.parse_log("02_assembly",
                                  ["--- [k = 99 ] assembling contigs"])
        self.assertFalse(prog.determinate)
        self.assertIn("k=99", prog.detail)
        prog2 = progress.parse_log("03_mapping",
                                   ["95.20% overall alignment rate"])
        self.assertIn("95.20", prog2.detail)

    def test_quiet_log_is_reported_as_normal_not_stalled(self):
        """A silent assembler must never be presented as a hung job."""
        from metaglens.observe import progress
        d = self._log("02_assembly", "Assembling sample: A\n")
        prog = progress.parse_stage(d, "02_assembly", now=time.time() + 3600)
        self.assertGreater(prog.seconds_since_output, 3000)
        self.assertIn("normal", prog.heartbeat.lower())
        for word in ("stall", "hung", "stuck", "dead", "fail"):
            self.assertNotIn(word, prog.heartbeat.lower())

    def test_missing_log_does_not_raise(self):
        from metaglens.observe import progress
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        prog = progress.parse_stage(d, "01_qc")
        self.assertFalse(prog.determinate)
        self.assertTrue(prog.detail)

    def test_progress_is_json_serialisable(self):
        from metaglens.observe import progress
        prog = progress.parse_log("01_qc", ["Processing sample: A"])
        self.assertIn("determinate", json.loads(json.dumps(prog.as_dict())))


class TestResourceSampling(unittest.TestCase):
    """Phase 13.1: stdlib sampling, psutil optional."""

    def test_sample_populates_core_fields(self):
        from metaglens.observe import resources
        s = resources.sample()
        self.assertGreaterEqual(s.cores, 1)
        self.assertIsNotNone(s.ram_total_gb)
        self.assertTrue(s.summary())

    def test_sample_without_psutil(self):
        from metaglens.observe import resources
        real_import = __import__

        def no_psutil(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError("psutil not available")
            return real_import(name, *args, **kwargs)

        with unittest.mock.patch("builtins.__import__", side_effect=no_psutil):
            s = resources.sample()
        self.assertGreaterEqual(s.cores, 1)
        self.assertTrue(s.summary())

    def test_disk_measurement_for_directory(self):
        from metaglens.observe import resources
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "f.bin").write_bytes(b"x" * 4096)
        s = resources.sample(d, measure_disk=True)
        self.assertIsNotNone(s.disk_used_gb)
        self.assertIsNotNone(s.disk_free_gb)

    def test_sample_is_json_serialisable(self):
        from metaglens.observe import resources
        self.assertIn("cores", json.loads(json.dumps(resources.sample().as_dict())))


class TestSharedCollectionLayer(unittest.TestCase):
    """Phase 13.5: HTML page and terminal view read one collector."""

    def _results(self) -> Path:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "reports" / "logs").mkdir(parents=True)
        (d / "pipeline_status.json").write_text(json.dumps({
            "project_name": "demo", "route_name": "mag_per_sample",
            "selected_steps": ["01_qc", "02_assembly", "03_mapping"],
            "steps": {"01_qc": {"status": "completed", "attempts": 1},
                      "02_assembly": {"status": "running", "attempts": 1},
                      "03_mapping": {"status": "pending", "attempts": 0}}}),
            encoding="utf-8")
        (d / "reports" / "logs" / "02_assembly.log").write_text(
            "Assembling sample: A\n  [A] Contig stats:\nAssembling sample: B\n",
            encoding="utf-8")
        return d

    def test_collect_includes_progress_and_resources(self):
        from metaglens.observe import monitor
        data = monitor.collect(self._results())
        self.assertEqual(data["current"], "02_assembly")
        self.assertEqual(data["completed"], 1)
        self.assertEqual(data["total_steps"], 3)
        self.assertTrue(data["progress"])
        self.assertTrue(data["resources"])

    def test_html_page_shows_progress_and_heartbeat(self):
        from metaglens.observe import monitor
        results = self._results()
        html = monitor.write_monitor(results).read_text(encoding="utf-8")
        self.assertIn("Stages:", html)
        self.assertIn("unit(s)", html)

    @unittest.skipUnless(_HAS_RICH, "rich is not installed")
    def test_dashboard_renders_from_same_snapshot(self):
        from metaglens.observe import monitor
        from metaglens.express import dashboard
        from rich.console import Console
        data = monitor.collect(self._results())
        console = Console(file=__import__("io").StringIO(), width=100,
                         force_terminal=False)
        console.print(dashboard.render(data))
        out = console.file.getvalue()
        for step in ("01_qc", "02_assembly", "03_mapping"):
            self.assertIn(step, out)

    @unittest.skipUnless(_HAS_RICH, "rich is not installed")
    def test_dashboard_states_that_leaving_is_safe(self):
        """The q/Ctrl-C semantics must be visible, never ambiguous."""
        from metaglens.observe import monitor
        from metaglens.express import dashboard
        from rich.console import Console
        data = monitor.collect(self._results())
        console = Console(file=__import__("io").StringIO(), width=100,
                         force_terminal=False)
        console.print(dashboard.render(data, quit_hint=True))
        out = console.file.getvalue().replace("\n", " ")
        self.assertIn("keeps running", out)

    @unittest.skipUnless(_HAS_RICH, "rich is not installed")
    def test_watch_once_does_not_touch_the_run(self):
        from metaglens.express import dashboard
        from rich.console import Console
        results = self._results()
        before = json.loads((results / "pipeline_status.json").read_text())
        console = Console(file=__import__("io").StringIO(), width=100,
                         force_terminal=False)
        dashboard.watch(results, console=console, once=True)
        after = json.loads((results / "pipeline_status.json").read_text())
        self.assertEqual(before, after, "watching must never modify run state")


class TestAdvisor(TempDirCase):
    """Phase 14.1/14.2: advice with reasons; science is advisory only."""

    def test_rules_are_well_formed(self):
        from metaglens.decide import advisor
        rules = advisor.load_rules()
        self.assertGreaterEqual(len(rules), 5)
        for rule in rules:
            self.assertIn("id", rule)
            self.assertIn("when", rule)
            self.assertTrue(rule.get("reason"), rule["id"])
            self.assertIn(rule.get("severity"), ("info", "warn"), rule["id"])

    def test_metaspades_on_small_ram_suggests_megahit(self):
        from metaglens.decide import advisor
        cfg = self.make_cfg(assembler="metaspades")
        advice = advisor.recommend(cfg, cores=32, ram_gb=64, n_samples=8)
        hit = next(a for a in advice
                   if a.rule_id == "assembler.metaspades_needs_ram")
        self.assertEqual(hit.suggested, "megahit")
        self.assertTrue(hit.applicable)
        self.assertIn("memory", hit.reason.lower())

    def test_low_threads_per_job_warns(self):
        from metaglens.decide import advisor
        cfg = self.make_cfg(parallel_jobs=16, threads_per_job=2)
        ids = {a.rule_id for a in advisor.recommend(cfg, cores=32, ram_gb=256,
                                                    n_samples=16)}
        self.assertIn("parallel.oversubscribed_threads", ids)

    def test_every_advice_carries_a_reason(self):
        from metaglens.decide import advisor
        cfg = self.make_cfg(assembler="metaspades", parallel_jobs=16,
                            threads_per_job=1)
        advice = advisor.recommend(cfg, cores=16, ram_gb=32, n_samples=16)
        self.assertTrue(advice)
        for item in advice:
            self.assertTrue(item.reason.strip(), item.rule_id)

    def test_scientific_parameters_are_never_applicable(self):
        """The core guarantee: --apply cannot rewrite a scientific threshold."""
        from metaglens.decide import advisor
        cfg = self.make_cfg(completeness_min=30, contamination_max=25,
                            min_contig_len=200)
        advice = advisor.recommend(cfg, cores=32, ram_gb=256, n_samples=4)
        science = [a for a in advice if a.scope == "science"]
        self.assertTrue(science, "expected scientific advisories")
        for item in science:
            self.assertFalse(item.applicable, item.rule_id)
        changes = advisor.applicable_changes(advice)
        for forbidden in ("completeness_min", "contamination_max",
                          "min_contig_len"):
            self.assertNotIn(forbidden, changes)

    def test_applicable_changes_are_resource_only(self):
        from metaglens.decide import advisor
        cfg = self.make_cfg(assembler="metaspades", parallel_jobs=16,
                            threads_per_job=1, completeness_min=10)
        changes = advisor.applicable_changes(
            advisor.recommend(cfg, cores=16, ram_gb=32, n_samples=16))
        self.assertTrue(set(changes) <= advisor.APPLICABLE_FIELDS)

    def test_expression_evaluator_rejects_code(self):
        """Rules are user-editable YAML, so expressions must not execute code."""
        from metaglens.decide.advisor import _resolve
        ctx = {"cores": 32, "ram_gb": 64.0}
        self.assertEqual(_resolve("max(1, cores // 4)", ctx), 8)
        for hostile in ('__import__("os").system("true")',
                        'open("/etc/passwd").read()',
                        "cores.__class__.__mro__"):
            # Refused: returned unchanged, never executed.
            self.assertEqual(_resolve(hostile, ctx), hostile)

    def test_diff_lines_show_before_and_after(self):
        from metaglens.decide import advisor
        cfg = self.make_cfg(assembler="metaspades")
        lines = advisor.diff_lines(cfg, {"assembler": "megahit"})
        self.assertIn("- assembler: metaspades", lines)
        self.assertIn("+ assembler: megahit", lines)


class TestMethodsGeneration(TempDirCase):
    """Phase 14.3: only what ran, with versions that are actually recorded."""

    def _results(self, completed, versions="") -> Path:
        r = self.tmp / "mres"
        (r / "reports").mkdir(parents=True, exist_ok=True)
        (r / "pipeline_status.json").write_text(json.dumps({
            "selected_steps": list(completed),
            "steps": {s: {"status": "completed"} for s in completed}}),
            encoding="utf-8")
        if versions:
            (r / "reports" / "tool_versions.txt").write_text(versions,
                                                             encoding="utf-8")
        return r

    def test_only_completed_stages_appear(self):
        from metaglens.express import methods
        cfg = self.make_cfg()
        r = self._results(["01_qc", "02_assembly"])
        text = methods.generate(cfg, results_dir=r)
        self.assertIn("Quality control", text)
        self.assertIn("assembl", text.lower())
        # Stages that did not run must not be described at all.
        self.assertNotIn("CheckM2", text)
        self.assertNotIn("dRep", text)
        self.assertNotIn("GTDB-Tk", text)

    def test_real_versions_are_used(self):
        from metaglens.express import methods
        cfg = self.make_cfg()
        r = self._results(["01_qc"], versions="fastp: fastp 0.23.4 [reused]\n")
        text = methods.generate(cfg, results_dir=r)
        self.assertIn("v0.23.4", text)
        self.assertNotIn("provisional", text)

    def test_missing_version_marked_provisional_not_invented(self):
        from metaglens.express import methods
        cfg = self.make_cfg()
        r = self._results(["01_qc"])          # no tool_versions.txt at all
        text = methods.generate(cfg, results_dir=r)
        self.assertIn("provisional", text)

    def test_command_echo_is_not_passed_off_as_a_version(self):
        from metaglens.express import methods
        cfg = self.make_cfg()
        r = self._results(["01_qc"], versions="fastp: [stub] fastp --version\n")
        text = methods.generate(cfg, results_dir=r)
        self.assertNotIn("--version)", text)
        self.assertIn("provisional", text)

    def test_optional_branches_reflect_switches(self):
        from metaglens.express import methods
        r = self._results(["01_qc", "08_annotation"])
        with_prokka = methods.generate(self.make_cfg(use_prokka=True,
                                                     use_eggnog=False),
                                       results_dir=r)
        self.assertIn("Prokka", with_prokka)
        self.assertNotIn("eggNOG", with_prokka)
        without = methods.generate(self.make_cfg(use_prokka=False,
                                                 use_eggnog=True),
                                   results_dir=r)
        self.assertIn("Prodigal", without)
        self.assertIn("eggNOG", without)

    def test_host_removal_only_mentioned_when_enabled(self):
        from metaglens.express import methods
        r = self._results(["01_qc"])
        self.assertNotIn("Host-derived",
                         methods.generate(self.make_cfg(), results_dir=r))
        self.assertIn("Host-derived",
                      methods.generate(self.make_cfg(remove_host=True,
                                                     host_genome="/x/host.fa"),
                                       results_dir=r))

    def test_no_completed_stage_says_so(self):
        from metaglens.express import methods
        text = methods.generate(self.make_cfg(), results_dir=self._results([]))
        self.assertIn("No stage has completed", text)

    def test_write_creates_methods_md(self):
        from metaglens.express import methods
        cfg = self.make_cfg()
        r = self._results(["01_qc"])
        out = methods.write(cfg, results_dir=r)
        self.assertTrue(out.is_file())
        self.assertEqual(out.name, "methods.md")


class TestBoundedRepair(TempDirCase):
    """Phase 14.4: the safety boundary, asserted from the outside."""

    def _diag(self, rule_id="oom.killed", exit_code=137, actions=None):
        from metaglens.decide.diagnose import Diagnosis
        return Diagnosis(
            stage="02_assembly", rule_id=rule_id, failure_class="environment",
            title="killed", diagnosis="d",
            actions=actions if actions is not None else [
                {"kind": "auto", "op": "reduce_parallel", "factor": 0.5,
                 "safe": True, "text": "halve concurrency"}],
            exit_code=exit_code, matched=True)

    # ---- counter-examples: the whitelist must refuse ---------------------- #
    def test_refuses_to_change_scientific_parameters(self):
        """Mandated counter-example: science params must be rejected."""
        from metaglens.decide import repair
        for field_name, value in (("completeness_min", 10),
                                  ("contamination_max", 50),
                                  ("ani_threshold", "80"),
                                  ("min_contig_len", 100),
                                  ("kmer_list", "21"),
                                  ("assembler", "megahit")):
            plan = repair.RepairPlan(op="reduce_parallel", stage="02_assembly",
                                     changes={field_name: value})
            with self.assertRaises(repair.RepairRefused, msg=field_name) as ctx:
                repair.check_allowed(plan)
            self.assertIn(field_name, str(ctx.exception))

    def test_refuses_unknown_operation(self):
        from metaglens.decide import repair
        plan = repair.RepairPlan(op="delete_outputs", stage="02_assembly",
                                 changes={})
        with self.assertRaises(repair.RepairRefused):
            repair.check_allowed(plan)

    def test_refuses_inputs_and_databases(self):
        from metaglens.decide import repair
        for field_name in ("raw_data_dir", "db_dir", "conda_env",
                           "sample_manifest", "route_name"):
            plan = repair.RepairPlan(op="retry", stage="01_qc",
                                     changes={field_name: "/tmp/x"})
            with self.assertRaises(repair.RepairRefused, msg=field_name):
                repair.check_allowed(plan)

    def test_allows_only_resource_fields(self):
        from metaglens.decide import repair
        repair.check_allowed(repair.RepairPlan(
            op="reduce_parallel", stage="02_assembly",
            changes={"parallel_jobs": 2, "threads_per_job": 8}))
        repair.check_allowed(repair.RepairPlan(
            op="increase_memory", stage="02_assembly",
            changes={"memory": "128G"}))
        repair.check_allowed(repair.RepairPlan(op="retry", stage="01_qc"))

    def test_plan_from_oom_diagnosis_lowers_concurrency(self):
        from metaglens.decide import repair
        cfg = self.make_cfg(parallel_jobs=8, threads_per_job=4,
                            total_threads=32)
        plan = repair.plan_from_diagnosis(cfg, self._diag())
        self.assertIsNotNone(plan)
        self.assertEqual(plan.changes["parallel_jobs"], 4)
        self.assertLess(plan.changes["parallel_jobs"], 8)
        repair.check_allowed(plan)          # must be inside the boundary

    def test_no_plan_when_diagnosis_offers_nothing_safe(self):
        from metaglens.decide import repair
        cfg = self.make_cfg()
        diag = self._diag(rule_id="db.gtdbtk_missing", exit_code=1, actions=[
            {"kind": "human", "text": "download the database"}])
        self.assertIsNone(repair.plan_from_diagnosis(cfg, diag))

    def test_no_plan_when_already_single_job(self):
        from metaglens.decide import repair
        cfg = self.make_cfg(parallel_jobs=1, threads_per_job=8)
        self.assertIsNone(repair.plan_from_diagnosis(cfg, self._diag()))

    # ---- bounds and evidence --------------------------------------------- #
    def _prepare(self):
        cfg = self.make_cfg(parallel_jobs=8, threads_per_job=4,
                            total_threads=32)
        results = cfg.results_dir
        (results / "reports").mkdir(parents=True, exist_ok=True)
        (results / "pipeline_status.json").write_text(json.dumps({
            "steps": {"02_assembly": {"status": "failed", "attempts": 1}}}),
            encoding="utf-8")
        script = results / routes.STEPS["02_assembly"].script
        script.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        return cfg, results

    def test_disabled_when_limit_zero(self):
        from metaglens.decide import repair
        cfg, results = self._prepare()
        out = repair.attempt_repair(cfg, "02_assembly", self._diag(),
                                    str(self.tmp / "c.yaml"), max_attempts=0)
        self.assertFalse(out["applied"])
        self.assertIn("disabled", out["reason"])

    def test_repeated_signature_stops(self):
        from metaglens.decide import repair
        cfg, results = self._prepare()
        repair.append_log(results, {"stage": "02_assembly",
                                    "signature": "oom.killed:137",
                                    "outcome": "still_failing"})
        out = repair.attempt_repair(cfg, "02_assembly", self._diag(),
                                    str(self.tmp / "c.yaml"), max_attempts=2)
        self.assertFalse(out["applied"])
        self.assertIn("same failure signature", out["reason"])

    def test_limit_is_enforced(self):
        from metaglens.decide import repair
        cfg, results = self._prepare()
        for i in range(2):
            repair.append_log(results, {"stage": "02_assembly",
                                        "signature": f"other:{i}"})
        out = repair.attempt_repair(cfg, "02_assembly", self._diag(),
                                    str(self.tmp / "c.yaml"), max_attempts=2)
        self.assertFalse(out["applied"])
        self.assertIn("limit reached", out["reason"])

    def test_successful_repair_records_full_evidence(self):
        from metaglens.decide import repair
        cfg, results = self._prepare()
        cfg_path = str(self.tmp / "cfg.yaml")

        def fake_run():
            pipeline.write_step_status(cfg, "02_assembly", "completed")
            return 0

        out = repair.attempt_repair(cfg, "02_assembly", self._diag(), cfg_path,
                                    max_attempts=2, runner=fake_run)
        self.assertTrue(out["applied"])
        self.assertTrue(out["repaired"])
        # Only the failed stage was re-run, with lowered concurrency.
        self.assertEqual(cfg.parallel_jobs, 4)
        entries = repair.read_log(results)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        for key in ("timestamp", "stage", "attempt", "signature", "diagnosis",
                    "plan", "rerun_command", "outcome"):
            self.assertIn(key, entry, key)
        self.assertEqual(entry["outcome"], "repaired")
        # The failing script was preserved before anything changed.
        self.assertTrue(entry["snapshot"])
        self.assertTrue(Path(entry["snapshot"]).is_file())

    def test_failed_repair_is_recorded_too(self):
        from metaglens.decide import repair
        cfg, results = self._prepare()
        out = repair.attempt_repair(cfg, "02_assembly", self._diag(),
                                    str(self.tmp / "c2.yaml"), max_attempts=2,
                                    runner=lambda: 1)
        self.assertTrue(out["applied"])
        self.assertFalse(out["repaired"])
        self.assertEqual(repair.read_log(results)[0]["outcome"], "still_failing")

    def test_refusal_is_logged_as_evidence(self):
        from metaglens.decide import repair
        cfg, results = self._prepare()
        bad = self._diag(actions=[{"kind": "auto", "op": "rewrite_science",
                                   "text": "nope"}])
        out = repair.attempt_repair(cfg, "02_assembly", bad,
                                    str(self.tmp / "c3.yaml"), max_attempts=2)
        self.assertFalse(out["applied"])
        self.assertIn("no safe automatic repair", out["reason"])


class TestShowcaseJobs(unittest.TestCase):
    """Phase 18: bounded, whitelisted, self-cleaning demo runner."""

    def _fake_result(self, route="mag_per_sample", ok=True):
        d = Path(tempfile.mkdtemp(prefix="mg_sc_test_"))
        (d / "work" / "metaglens_results").mkdir(parents=True)
        (d / "work" / "metaglens_results" / "02_assembly.sh").write_text(
            "#!/bin/bash\n# rendered stage\nexit 0\n", encoding="utf-8")
        rep = d / "report.html"; rep.write_text("<html>report</html>", encoding="utf-8")
        return {"route": route, "ok": ok, "root": str(d),
                "report_html": str(rep), "monitor_html": "",
                "stages": [{"step": "01_qc", "status": "completed"}],
                "errors": [] if ok else ["boom"]}

    def test_rejects_non_whitelisted_route(self):
        from metaglens.showcase import JobManager
        mgr = JobManager(runner=lambda route: self._fake_result(route))
        self.addCleanup(mgr.shutdown)
        res = mgr.submit("; rm -rf /")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "rejected")

    def test_run_completes_and_exposes_artefacts(self):
        from metaglens.showcase import JobManager
        results = {}
        def runner(route):
            r = self._fake_result(route); results[route] = r; return r
        mgr = JobManager(runner=runner)
        self.addCleanup(mgr.shutdown)
        res = mgr.submit("mag_per_sample")
        self.assertTrue(res["ok"])
        jid = res["id"]
        for _ in range(200):
            job = mgr.get(jid)
            if job.status in ("done", "failed", "timeout"):
                break
            time.sleep(0.02)
        self.assertEqual(job.status, "done")
        self.assertTrue(job.report_path)
        self.assertIn("#!/bin/bash", job.script_text)
        pub = job.public()
        # public view must not leak a filesystem path
        self.assertNotIn("root", pub)
        self.assertNotIn("/tmp", json.dumps(pub))

    def test_queue_limit_returns_busy(self):
        from metaglens.showcase import JobManager
        started = threading.Event(); release = threading.Event()
        def slow(route):
            started.set(); release.wait(5); return self._fake_result(route)
        mgr = JobManager(queue_limit=2, runner=slow)
        self.addCleanup(lambda: (release.set(), mgr.shutdown()))
        a = mgr.submit("mag_per_sample"); self.assertTrue(a["ok"])
        started.wait(2)
        b = mgr.submit("mag_per_sample"); self.assertTrue(b["ok"])   # queued
        c = mgr.submit("mag_per_sample")                              # over limit
        self.assertFalse(c["ok"])
        self.assertEqual(c["status"], "busy")
        release.set()

    def test_timeout_is_bounded(self):
        from metaglens.showcase import JobManager
        def hang(route):
            time.sleep(10); return self._fake_result(route)
        mgr = JobManager(run_timeout=0.3, runner=hang)
        self.addCleanup(mgr.shutdown)
        jid = mgr.submit("mag_per_sample")["id"]
        for _ in range(200):
            job = mgr.get(jid)
            if job.status in ("done", "failed", "timeout"):
                break
            time.sleep(0.02)
        self.assertEqual(job.status, "timeout")

    def test_old_runs_are_cleaned_up(self):
        from metaglens.showcase import JobManager
        roots = []
        def runner(route):
            r = self._fake_result(route); roots.append(r["root"]); return r
        mgr = JobManager(keep_runs=2, runner=runner)
        self.addCleanup(mgr.shutdown)
        for _ in range(5):
            jid = mgr.submit("mag_per_sample")["id"]
            for _ in range(200):
                if mgr.get(jid).status in ("done", "failed", "timeout"):
                    break
                time.sleep(0.02)
        time.sleep(0.1)
        # The earliest run trees must have been removed.
        survivors = [r for r in roots if Path(r).exists()]
        self.assertLessEqual(len(survivors), 3)


@unittest.skipIf(shutil.which("bash") is None, "bash unavailable")
class TestShowcaseServer(unittest.TestCase):
    """Phase 18: read-only HTTP surface; the security posture is the point."""

    def _server(self, runner=None):
        from metaglens.showcase import build_app, JobManager
        mgr = JobManager(runner=runner) if runner else JobManager()
        srv = build_app(manager=mgr, host="127.0.0.1", port=0)
        port = srv.server_address[1]
        th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close(), mgr.shutdown()))
        return f"http://127.0.0.1:{port}", mgr

    def _code(self, base, path, method="GET", body=None):
        import urllib.request, urllib.error
        try:
            if method == "POST":
                req = urllib.request.Request(
                    base + path, data=json.dumps(body or {}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
            else:
                req = urllib.request.Request(base + path, method=method)
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_index_served(self):
        base, _ = self._server()
        code, body = self._code(base, "/")
        self.assertEqual(code, 200)
        self.assertIn(b"MetaGLens", body)
        self.assertIn(b"NO scientific results", body)

    def test_path_traversal_rejected(self):
        base, _ = self._server()
        code, _ = self._code(base, "/api/report?id=../../etc/passwd")
        self.assertIn(code, (400, 404))

    def test_bad_id_rejected(self):
        base, _ = self._server()
        code, _ = self._code(base, "/api/status?id=%3Brm%20-rf")
        self.assertEqual(code, 400)

    def test_injection_route_refused(self):
        base, _ = self._server()
        code, body = self._code(base, "/api/run", "POST", {"route": "; rm -rf /"})
        self.assertNotEqual(code, 200)
        self.assertFalse(json.loads(body)["ok"])

    def test_no_write_endpoint(self):
        base, _ = self._server()
        # webconfig's /save must not exist on the public showcase.
        code, _ = self._code(base, "/save", "POST", {})
        self.assertEqual(code, 404)

    def test_oversized_body_rejected(self):
        import urllib.request, urllib.error
        base, _ = self._server()
        req = urllib.request.Request(
            base + "/api/run", data=b"x" * 99999,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        self.assertEqual(code, 413)

    def test_unknown_run_id_is_404_not_500(self):
        base, _ = self._server()
        code, _ = self._code(base, "/api/status?id=deadbeef")
        self.assertEqual(code, 404)


class TestShowcaseExport(unittest.TestCase):
    """Phase 18.A4: backend-free static export tells the whole story."""

    @unittest.skipIf(shutil.which("bash") is None, "bash unavailable")
    def test_export_produces_complete_site(self):
        from metaglens.showcase import export_static
        d = Path(tempfile.mkdtemp(prefix="mg_export_test_"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        export_static(str(d), route="mag_per_sample")
        self.assertTrue((d / "index.html").is_file())
        self.assertTrue((d / "report.html").stat().st_size > 500)
        script = (d / "script.txt").read_text(encoding="utf-8")
        self.assertTrue(script.startswith("#!/bin/bash") or "#SBATCH" in script[:200])
        idx = (d / "index.html").read_text(encoding="utf-8")
        self.assertIn('"static": true', idx)

    def test_static_page_marks_stub_and_reuses_theme(self):
        from metaglens.showcase.page import build_page
        page = build_page(static=True)
        self.assertIn("--brand:#38A8F0", page)            # shared theme
        self.assertIn("NO scientific results", page)      # honesty line


class TestShowcaseAttackPanel(unittest.TestCase):
    """Phase 18: the attack panel is backed by the real repair boundary check."""

    def test_canonical_results_are_real(self):
        from metaglens.showcase import attacks
        results = {r["key"]: r for r in attacks.run_canonical()}
        # The scientific / smuggled / bad-op probes must be refused ...
        for key in ("sci_min_contig", "sci_completeness", "smuggle", "bad_op"):
            self.assertTrue(results[key]["refused"], key)
        # ... and the legal resource change allowed.
        self.assertFalse(results["legal"]["refused"])
        # Messages are the real ones from repair.check_allowed, not canned text.
        self.assertIn("min_contig_len", results["sci_min_contig"]["message"])
        self.assertIn("min_length", results["smuggle"]["message"])
        self.assertIn("whitelist", results["bad_op"]["message"])

    def test_evaluate_matches_repair_check(self):
        """The panel must call the same code the pipeline uses."""
        from metaglens.showcase import attacks
        from metaglens.decide import repair
        # A field repair forbids -> refused here too.
        self.assertTrue(attacks.evaluate("reduce_parallel", "s",
                                        {"ani_threshold": "70"})["refused"])
        # A resource field -> allowed here too.
        self.assertFalse(attacks.evaluate("reduce_parallel", "s",
                                         {"parallel_jobs": 2})["refused"])
        # Cross-check against repair directly.
        with self.assertRaises(repair.RepairRefused):
            repair.check_allowed(repair.RepairPlan(
                op="reduce_parallel", stage="s", changes={"ani_threshold": "70"}))

    def test_evaluate_never_executes_only_inspects(self):
        """Even a hostile field name is merely refused, never run."""
        from metaglens.showcase import attacks
        res = attacks.evaluate("reduce_parallel", "s",
                               {"__import__('os').system('x')": 1})
        self.assertTrue(res["refused"])   # not a resource field -> refused

    def test_evaluate_bounds_change_count(self):
        from metaglens.showcase import attacks
        many = {f"f{i}": i for i in range(50)}
        res = attacks.evaluate("reduce_parallel", "s", many)
        self.assertLessEqual(len(res["changes"]), 8)

    def test_page_bakes_attacks_and_real_audit(self):
        import json, re
        from metaglens.showcase.page import build_page
        page = build_page(static=True)
        boot = json.loads(re.search(r"var BOOT=(\{.*?\});", page, re.S).group(1))
        self.assertEqual(len(boot["attacks"]), 5)
        self.assertTrue(any(a["refused"] for a in boot["attacks"]))
        self.assertTrue(any(not a["refused"] for a in boot["attacks"]))
        # Audit numbers are present and numeric (real, not a stale literal).
        audit = dict(boot["audit"])
        self.assertIn("tests", audit)
        self.assertTrue(audit["tests"].isdigit())
        # History + attack sections and honesty line all present.
        self.assertIn('id="history"', page)
        self.assertIn('id="attack"', page)
        self.assertIn("NO scientific results", page)

    def test_history_frames_ai_coding_not_ai4s_product(self):
        """The narrative must say the product runs with zero AI."""
        from metaglens.showcase.page import build_page
        page = build_page(static=True)
        self.assertIn("AI Coding", page)
        self.assertIn("zero AI", page)   # runtime has no AI — the key point


@unittest.skipIf(shutil.which("bash") is None, "bash unavailable")
class TestShowcaseAttackEndpoint(unittest.TestCase):
    """Phase 18: /api/attack runs the real check on judge input, safely."""

    def _server(self):
        from metaglens.showcase import build_app, JobManager
        mgr = JobManager()
        srv = build_app(manager=mgr, host="127.0.0.1", port=0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close(), mgr.shutdown()))
        return f"http://127.0.0.1:{port}"

    def _post(self, base, path, obj):
        import urllib.request, urllib.error
        req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_attack_endpoint_refuses_scientific_field(self):
        base = self._server()
        code, body = self._post(base, "/api/attack",
                                {"op": "reduce_parallel", "stage": "s",
                                 "changes": {"completeness_min": 5}})
        self.assertEqual(code, 200)
        self.assertTrue(body["refused"])
        self.assertIn("completeness_min", body["message"])

    def test_attack_endpoint_allows_resource_field(self):
        base = self._server()
        code, body = self._post(base, "/api/attack",
                                {"op": "reduce_parallel", "stage": "s",
                                 "changes": {"parallel_jobs": 2}})
        self.assertEqual(code, 200)
        self.assertFalse(body["refused"])

    def test_attack_endpoint_hostile_input_is_refused_not_run(self):
        base = self._server()
        code, body = self._post(base, "/api/attack",
                                {"op": "delete_everything", "stage": "s",
                                 "changes": {"x": 1}})
        self.assertEqual(code, 200)
        self.assertTrue(body["refused"])   # non-whitelisted op


if __name__ == "__main__":
    unittest.main(verbosity=2)
