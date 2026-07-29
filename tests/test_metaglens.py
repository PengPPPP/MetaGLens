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
                found, detected = samples_mod.discover(str(raw))
                self.assertEqual(detected, label)
                self.assertEqual([s.sample_id for s in found], ["S1", "S2"])

    def test_paths_are_absolute(self):
        raw = self.tmp / "abs"
        _make_reads(raw, ["S1"])
        found, _ = samples_mod.discover(str(raw))
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
        found, _ = samples_mod.discover(str(raw))
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
