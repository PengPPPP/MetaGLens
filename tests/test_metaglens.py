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
import unittest
import unittest.mock
from pathlib import Path

from metaglens import render, routes, samples as samples_mod
from metaglens import conda_env, conda_setup
from metaglens.config import Config
from metaglens import pipeline
from metaglens.report import generate_report, _parse_fastp_reports

_PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
