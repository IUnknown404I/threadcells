"""Release-preparation assets remain local, reproducible, and explicit."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def _candidate_builder_module():
    spec = spec_from_file_location(
        "build_local_candidate", ROOT / "scripts" / "build_local_candidate.py"
    )
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate_verifier_module():
    spec = spec_from_file_location(
        "verify_local_candidate", ROOT / "scripts" / "verify_local_candidate.py"
    )
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _public_audit_module():
    spec = spec_from_file_location(
        "audit_public_surface", ROOT / "scripts" / "audit_public_surface.py"
    )
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate_with_pyproject(tmp_path: Path, pyproject: str) -> Path:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    web = candidate / "web"
    web.mkdir()
    (web / "package.json").write_text('{"dependencies": {}}\n', encoding="utf-8")
    (web / "package-lock.json").write_text('{"packages": {}}\n', encoding="utf-8")
    return candidate


def _valid_verification_candidate(tmp_path: Path) -> Path:
    candidate = tmp_path / "candidate"
    asset = candidate / "web" / "public" / "brand-asset.txt"
    asset.parent.mkdir(parents=True)
    asset.write_text("brand asset\n", encoding="utf-8")
    asset_checksum = hashlib.sha256(asset.read_bytes()).hexdigest()
    brand = candidate / "brand"
    brand.mkdir()
    pack = brand / "threadcells_brand_asset_pack"
    for relative in (
        "logos/bg-101622",
        "logos/bg-black",
        "symbols/bg-101622",
        "symbols/bg-black",
        "favicons/bg-101622",
        "favicons/bg-black",
    ):
        (pack / relative).mkdir(parents=True, exist_ok=True)
    (pack / "README.md").write_text("# ThreadCells Brand Asset Pack\n", encoding="utf-8")
    pack_source = pack / "logos" / "bg-101622" / "brand-source.png"
    pack_source.write_bytes(b"canonical brand source\n")
    pack_files = [pack / "README.md", pack_source]
    (pack / "SHA256SUMS.txt").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.relative_to(pack).as_posix()}\n"
            for path in pack_files
        ),
        encoding="utf-8",
    )
    (brand / "ASSET_MANIFEST.json").write_text(
        json.dumps(
            {
                "schema": 2,
                "canonical_pack_root": "brand/threadcells_brand_asset_pack",
                "canonical_pack_checksum_file": "brand/threadcells_brand_asset_pack/SHA256SUMS.txt",
                "assets": [
                    {
                        "source": "logos/bg-101622/brand-source.png",
                        "destination": "web/public/brand-asset.txt",
                        "sha256": asset_checksum,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (brand / "SHA256SUMS").write_text(
        f"{asset_checksum}  ../web/public/brand-asset.txt\n", encoding="utf-8"
    )
    (candidate / "README.md").write_text("# Candidate\n", encoding="utf-8")
    readme_checksum = hashlib.sha256((candidate / "README.md").read_bytes()).hexdigest()
    docs = candidate / "docs"
    docs.mkdir()
    (docs / "DOCS_MANIFEST.json").write_text(
        json.dumps({"documents": [{"source": "README.md"}]}) + "\n", encoding="utf-8"
    )
    (candidate / "web" / "public" / "docs-bundle.json").write_text(
        json.dumps(
            {
                "commit": "a" * 40,
                "documents": [{"source": "README.md", "sha256": readme_checksum}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (candidate / "scripts").mkdir()
    (candidate / "scripts" / "verify_local_candidate.py").write_text(
        "# verifier\n", encoding="utf-8"
    )
    starter = candidate / "examples" / "threadcells-starter"
    starter.mkdir(parents=True)
    (starter / "README.md").write_text("# Starter\n", encoding="utf-8")
    adapter = candidate / "examples" / "provider-adapters" / "threadcells-echo"
    adapter.mkdir(parents=True)
    (adapter / "README.md").write_text("# Adapter\n", encoding="utf-8")
    (adapter / "threadcells-provider.json").write_text("{}\n", encoding="utf-8")
    schemas = candidate / "schemas" / "v1"
    schemas.mkdir(parents=True)
    for name in ("adapter-manifest", "capabilities", "profile", "provider-config"):
        (schemas / f"{name}.schema.json").write_text("{}\n", encoding="utf-8")
    packages = candidate / "packages"
    packages.mkdir()
    with ZipFile(
        packages / "threadcells-0.1.0-py3-none-any.whl", "w", compression=ZIP_DEFLATED
    ) as wheel:
        for name in ("adapter-manifest", "capabilities", "profile", "provider-config"):
            wheel.writestr(f"cli_agent_orchestrator/public_schemas/v1/{name}.schema.json", "{}\n")
        wheel.writestr("cli_agent_orchestrator/api/main.py", '@app.get("/settings/{path:path}")\n')
        wheel.writestr(
            "threadcells-0.1.0.dist-info/entry_points.txt",
            "[console_scripts]\nthreadcells = package:main\nthreadcells-server = package:server\n"
            "threadcells-housekeeping = package:housekeeping\n",
        )
    _candidate_builder_module().write_metadata(
        candidate,
        revision="a" * 40,
        version="0.1.0",
        components=[{"type": "application", "name": "threadcells", "version": "0.1.0"}],
        node_licenses={},
    )
    return candidate


def test_docs_bundle_renders_canonical_navigation(tmp_path: Path) -> None:
    output = tmp_path / "docs-bundle.json"
    subprocess.run(
        [sys.executable, "scripts/build_docs_bundle.py", "--output", str(output)],
        cwd=ROOT,
        check=True,
    )
    bundle = json.loads(output.read_text(encoding="utf-8"))
    documents = {document["slug"]: document for document in bundle["documents"]}
    assert {
        "overview",
        "installation",
        "first-agent",
        "remote-access",
        "operator-authorization",
        "telegram-notifications",
        "statistics",
        "deployment",
        "release-process",
        "issues",
    } <= documents.keys()
    assert "owner-decisions" not in documents
    assert len(documents) == 31
    assert "](/docs/getting-started)" in documents["installation"]["markdown"]
    assert "](/media/screenshots/threadcells-home.webp)" in documents["web-ui"]["markdown"]
    assert (
        "](/media/screenshots/threadcells-housekeeping.webp)"
        in documents["housekeeping"]["markdown"]
    )


def test_docs_bundle_check_ignores_only_the_self_referential_commit() -> None:
    from scripts import build_docs_bundle

    expected = json.dumps({"commit": "b" * 40, "documents": [{"sha256": "current"}]})
    prior_revision = json.dumps({"commit": "a" * 40, "documents": [{"sha256": "current"}]})
    stale_content = json.dumps({"commit": "a" * 40, "documents": [{"sha256": "stale"}]})

    assert build_docs_bundle.matches_tracked_bundle(prior_revision, expected)
    assert not build_docs_bundle.matches_tracked_bundle(stale_content, expected)


def test_local_candidate_tools_are_safe_by_default(tmp_path: Path) -> None:
    installer = ROOT / "scripts" / "install-threadcells.sh"
    assert os.stat(installer).st_mode & stat.S_IXUSR
    candidate = tmp_path / "candidate"
    (candidate / "packages").mkdir(parents=True)
    (candidate / "candidate-manifest.json").write_text("{}\n", encoding="utf-8")
    (candidate / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")
    (candidate / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (candidate / "packages" / "threadcells-0.1.0a1-py3-none-any.whl").write_bytes(b"test")
    checksum_entries = []
    for path in sorted(candidate.rglob("*")):
        if path.is_file():
            checksum_entries.append(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(candidate)}\n"
            )
    (candidate / "SHA256SUMS").write_text("".join(checksum_entries), encoding="utf-8")
    prefix = tmp_path / "new-prefix"
    result = subprocess.run(
        [str(installer), "--source", str(candidate), "--dry-run", "--prefix", str(prefix)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    assert "no provider credentials" in result.stdout
    assert not prefix.exists()
    existing = tmp_path / "existing-prefix"
    existing.mkdir()
    refused = subprocess.run(
        [str(installer), "--source", str(candidate), "--dry-run", "--prefix", str(existing)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert refused.returncode != 0
    assert "refusing to overwrite" in refused.stderr


def test_public_candidate_scope_has_no_publication_workflow_or_source_pack() -> None:
    assert not (ROOT / ".github" / "workflows" / "release.yml").exists()
    assert not (ROOT / ".github" / "workflows" / "publish-to-pypi.yml").exists()
    assert not (ROOT / "brand" / "threadcells" / "source-pack").exists()
    assert (ROOT / "scripts" / "build_local_candidate.py").exists()
    assert (ROOT / "deployment" / "promote-ops-p1.py").exists()


def test_candidate_pruning_keeps_public_website_assets_only(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    for directory in (
        "brand",
        "docs/assets",
        "examples/threadcells-starter",
        "examples/provider-adapters/threadcells-echo",
        "packages",
        "scripts",
        "src/cli_agent_orchestrator/public_schemas/v1",
        "web/public",
        "website/public/media",
        "website/app",
        "website/node_modules/package",
    ):
        (candidate / directory).mkdir(parents=True, exist_ok=True)
    (candidate / "docs/DOCS_MANIFEST.json").write_text(
        json.dumps({"documents": []}) + "\n", encoding="utf-8"
    )
    (candidate / "website/public/favicon.ico").write_bytes(b"favicon")
    (candidate / "website/public/media/threadcells-social.png").write_bytes(b"social")
    (candidate / "website/app/page.tsx").write_text("private build source\n", encoding="utf-8")
    (candidate / "website/node_modules/package/index.js").write_text(
        "dependency build source\n", encoding="utf-8"
    )

    _candidate_builder_module().prune_to_install_candidate(candidate)

    assert (candidate / "website/public/favicon.ico").read_bytes() == b"favicon"
    assert (candidate / "website/public/media/threadcells-social.png").read_bytes() == b"social"
    assert not (candidate / "website/app").exists()
    assert not (candidate / "website/node_modules").exists()


def test_public_media_inventory_is_live_bounded_and_shared_by_docs() -> None:
    names = {
        "threadcells-home",
        "threadcells-session-workflow",
        "threadcells-agents",
        "threadcells-housekeeping",
        "threadcells-telegram",
        "threadcells-capacity",
    }
    masters = ROOT / "launch-media" / "output" / "screenshots"
    website_media = ROOT / "website" / "public" / "media" / "screenshots"
    runtime_media = ROOT / "web" / "public" / "media" / "screenshots"

    assert {path.stem for path in masters.glob("*.png")} == names
    assert {path.stem for path in website_media.glob("*.webp")} == names
    assert {path.stem for path in runtime_media.glob("*.webp")} == names
    for name in names:
        master = masters / f"{name}.png"
        website = website_media / f"{name}.webp"
        runtime = runtime_media / f"{name}.webp"
        assert master.stat().st_size < 300_000
        assert website.stat().st_size < 150_000
        assert website.read_bytes() == runtime.read_bytes()

    demo_master = ROOT / "launch-media" / "output" / "demo" / "threadcells-demo.webm"
    website_demo = ROOT / "website" / "public" / "media" / "demo"
    assert demo_master.stat().st_size < 2_000_000
    assert (website_demo / "threadcells-demo.webm").read_bytes() == demo_master.read_bytes()
    assert (website_demo / "threadcells-demo.mp4").stat().st_size < 2_000_000
    assert not (ROOT / "launch-media" / "output" / "demo" / "threadcells-demo.gif").exists()

    capture = (ROOT / "launch-media" / "capture-product.mjs").read_text(encoding="utf-8")
    assert "live-loopback-production" in capture
    assert "createFixtureServer" not in capture
    assert "Synthetic launch-media fixture" not in capture


def test_release_builder_and_public_config_are_host_neutral_and_offline() -> None:
    config_path = ROOT / "src/cli_agent_orchestrator/config/cao-operations.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["release_roots"] == ["/var/lib/threadcells/releases"]
    assert config["release_staging_lock"] == "/var/lib/threadcells/release-staging.lock"
    assert config["release_metadata"] == "/var/lib/threadcells/release-metadata.json"
    assert config["active_release_link"] == "/var/lib/threadcells/active"
    assert config["release_admin_group"] == "threadcells-release-admin"
    assert config["release_control_uid"] == 0
    assert config["worktree_durable_refs"] == []
    assert config["reproducible_cache_roots"] == []
    for cache in config["package_caches"]:
        argument = cache["path_argument"]
        assert cache["command"][cache["command"].index(argument) + 1] == cache["path"]
    audit = _public_audit_module()
    assert config_path.relative_to(ROOT).as_posix() in audit.PUBLIC_FILES
    assert (
        audit.forbidden_findings(
            config_path.relative_to(ROOT).as_posix(),
            config_path.read_text(encoding="utf-8"),
        )
        == []
    )

    builder = (ROOT / "scripts/build_local_candidate.py").read_text(encoding="utf-8")
    assert '"uv",\n            "build",\n            "--offline"' in builder
    hook = (ROOT / "hatch_build.py").read_text(encoding="utf-8")
    assert 'os.environ.get("THREADCELLS_SOURCE_REVISION")' in hook
    assert 'install.extend(("--offline", "--ignore-scripts"))' in hook
    workflow = (ROOT / ".github/workflows/threadcells-local-candidate.yml").read_text(
        encoding="utf-8"
    )
    assert "scripts/audit_public_surface.py --candidate" in workflow
    assert '--wheel "$wheel"' in workflow


def test_wheel_public_audit_rejects_private_operations_path(tmp_path: Path) -> None:
    wheel = tmp_path / "threadcells-test.whl"
    with ZipFile(wheel, "w", compression=ZIP_DEFLATED) as archive:
        for name in ("adapter-manifest", "capabilities", "profile", "provider-config"):
            archive.writestr(f"cli_agent_orchestrator/public_schemas/v1/{name}.schema.json", "{}\n")
        archive.writestr(
            "cli_agent_orchestrator/config/cao-operations.json",
            '{"release_roots":["/srv/private/releases"]}\n',
        )

    assert _public_audit_module().audit_wheel(wheel) == [
        "cli_agent_orchestrator/config/cao-operations.json: /srv/"
    ]


def test_dependency_components_represent_all_current_direct_python_requirements(
    tmp_path: Path,
) -> None:
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    requirements = tomllib.loads(pyproject_text)["project"]["dependencies"]
    candidate = _candidate_with_pyproject(tmp_path, pyproject_text)

    components = _candidate_builder_module().dependency_components(candidate, "0.1.0a1")
    direct_components = [
        component
        for component in components
        if component.get("properties")
        == [{"name": "threadcells:source", "value": "pyproject direct requirement"}]
    ]

    assert len(requirements) == 15
    assert [component["version"] for component in direct_components] == requirements
    assert len(direct_components) == len(requirements)
    assert len({component["bom-ref"] for component in direct_components}) == len(requirements)
    assert components == _candidate_builder_module().dependency_components(candidate, "0.1.0a1")


def test_dependency_components_preserve_pep508_extras_markers_and_constraints(
    tmp_path: Path,
) -> None:
    requirements = [
        'example_pkg[feature]>=1.0,<2.0; python_version >= "3.10"',
        'another.pkg===1.2.3; implementation_name == "cpython"',
    ]
    candidate = _candidate_with_pyproject(
        tmp_path,
        '[project]\nname = "test"\ndependencies = [\n'
        + "\n".join(f"  {json.dumps(requirement)}," for requirement in requirements)
        + "\n]\n",
    )

    components = _candidate_builder_module().dependency_components(candidate, "0.1.0")
    direct_components = components[1:]

    assert [component["name"] for component in direct_components] == ["example_pkg", "another.pkg"]
    assert [component["version"] for component in direct_components] == requirements
    assert [component["bom-ref"] for component in direct_components] == [
        "urn:threadcells:direct-python:example-pkg:"
        "71de88385b2889e1c81d22401eda633d7753cc5fc4711b9457af3d831b595dee",
        "urn:threadcells:direct-python:another-pkg:"
        "ffe6ea55d01fa88735dd8853d5d13d4efec7ab3fa5f616ccbca6592f24a65f1d",
    ]


def test_final_brand_asset_manifest_matches_runtime_assets() -> None:
    manifest = json.loads((ROOT / "brand" / "ASSET_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "ThreadCells Brand Asset Pack"
    pack = ROOT / manifest["canonical_pack_root"]
    assert (
        (pack / "README.md")
        .read_text(encoding="utf-8")
        .startswith("# ThreadCells Brand Asset Pack")
    )
    assert not _candidate_verifier_module().verify_brand_assets(ROOT)
    for asset in manifest["assets"]:
        path = ROOT / asset["destination"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == asset["sha256"]
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'href="/favicon.ico"' in index
    assert 'href="/manifest.webmanifest"' in index
    assert 'content="/threadcells-og-1200x630.png"' in index


def test_pwa_manifest_and_worker_are_conservative() -> None:
    manifest = json.loads((ROOT / "web" / "public" / "manifest.webmanifest").read_text())
    assert manifest["id"] == manifest["start_url"] == manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert {icon["sizes"] for icon in manifest["icons"]} >= {"192x192", "512x512"}
    assert any("maskable" in icon.get("purpose", "") for icon in manifest["icons"])
    for icon in manifest["icons"]:
        assert (ROOT / "web" / "public" / icon["src"].lstrip("/")).is_file()

    worker = (ROOT / "web" / "public" / "sw.js").read_text(encoding="utf-8")
    assert "pathname.startsWith('/assets/')" in worker
    for dynamic_path in ("/api/", "/operator/", "/sessions", "/workflows", "/terminals"):
        assert dynamic_path not in worker


def test_candidate_verifier_covers_nested_brand_checksum_file(tmp_path: Path) -> None:
    candidate = _valid_verification_candidate(tmp_path)

    assert _candidate_verifier_module().verify(candidate) == []
    manifest = json.loads((candidate / "candidate-manifest.json").read_text(encoding="utf-8"))
    assert "brand/SHA256SUMS" in {entry["path"] for entry in manifest["files"]}
    assert "  brand/SHA256SUMS\n" in (candidate / "SHA256SUMS").read_text(encoding="utf-8")


def test_candidate_verifier_rejects_tampered_brand_asset(tmp_path: Path) -> None:
    candidate = _valid_verification_candidate(tmp_path)
    (candidate / "web" / "public" / "brand-asset.txt").write_text("tampered\n", encoding="utf-8")

    errors = _candidate_verifier_module().verify(candidate)

    assert "brand asset mismatch: web/public/brand-asset.txt" in errors
    assert "checksum mismatch: web/public/brand-asset.txt" in errors


def test_candidate_verifier_rejects_tampered_brand_checksum_file(tmp_path: Path) -> None:
    candidate = _valid_verification_candidate(tmp_path)
    (candidate / "brand" / "SHA256SUMS").write_text(
        f"{'0' * 64}  ../web/public/brand-asset.txt\n", encoding="utf-8"
    )

    errors = _candidate_verifier_module().verify(candidate)

    assert "checksum mismatch: brand/SHA256SUMS" in errors
    assert "candidate manifest mismatch: brand/SHA256SUMS" in errors
    assert "brand checksum mismatch: ../web/public/brand-asset.txt" in errors


def test_dependency_owner_packet_is_deterministic_and_non_clearance(tmp_path: Path) -> None:
    candidate = _candidate_with_pyproject(
        tmp_path, '[project]\nname = "test"\ndependencies = ["example>=1"]\n'
    )
    sbom = {
        "components": [
            {"type": "application", "name": "threadcells", "version": "0.1.0"},
            {"type": "library", "name": "example", "version": "example>=1", "properties": []},
        ]
    }
    builder = _candidate_builder_module()
    builder.write_dependency_review_packet(candidate, sbom)
    first = (candidate / "DEPENDENCY_REVIEW.md").read_text(encoding="utf-8")
    builder.write_dependency_review_packet(candidate, sbom)
    assert (candidate / "DEPENDENCY_REVIEW.md").read_text(encoding="utf-8") == first
    assert "not a license clearance" in first
    assert "owner review required" in first
