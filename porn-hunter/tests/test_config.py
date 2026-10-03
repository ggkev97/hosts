import textwrap
from pathlib import Path

import pytest

from porn_hunter.config import load_config
from porn_hunter.errors import ConfigError

ROOT = Path(__file__).resolve().parent.parent


def write(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_shipped_config_is_valid():
    cfg = load_config(ROOT / "config.yaml")
    assert cfg.model.name == "ViT-B-32"
    assert cfg.model.pretrained == "laion2b_s34b_b79k"
    assert cfg.enabled_sites() == ["pornhub", "xvideos", "xhamster"]
    assert cfg.download.quality == "best" and cfg.download.format == "mp4"
    assert cfg.path("output_dir") == ROOT / "downloads"


def test_empty_file_uses_defaults(tmp_path):
    cfg = load_config(write(tmp_path, ""))
    assert cfg.search.top_k == 20
    assert set(cfg.sites) == {"pornhub", "xvideos", "xhamster"}


def test_overrides_and_relative_paths(tmp_path):
    cfg = load_config(write(tmp_path, """
        paths: {output_dir: out}
        download: {quality: 720p, max_per_run: 2}
        sites: {xvideos: {enabled: false}}
    """))
    assert cfg.path("output_dir") == tmp_path.resolve() / "out"
    assert cfg.download.quality == "720p"
    assert cfg.enabled_sites() == ["pornhub", "xhamster"]
    assert cfg.sites["xvideos"].first_page == 0  # default retained


def test_absolute_path_kept(tmp_path):
    cfg = load_config(write(tmp_path, f"paths: {{output_dir: {tmp_path}/abs}}"))
    assert cfg.path("output_dir") == tmp_path / "abs"


def test_auto_queue_parsed(tmp_path):
    cfg = load_config(write(tmp_path, """
        scheduler:
          auto_queue:
            - {query: "red dress", top_k: 3, min_score: 0.2}
    """))
    item = cfg.scheduler.auto_queue[0]
    assert (item.query, item.top_k, item.min_score) == ("red dress", 3, 0.2)


@pytest.mark.parametrize("body, needle", [
    ("bogus: 1", "unknown top-level"),
    ("download: {qualty: best}", "unknown option"),
    ("download: {quality: 4k}", "download.quality"),
    ("download: {format: avi}", "download.format"),
    ("download: {min_delay: 5, max_delay: 1}", "min_delay <= max_delay"),
    ("download: {max_per_run: abc}", "expected int"),
    ("scheduler: {index_cron: 'nope'}", "invalid cron"),
    ("logging: {level: LOUD}", "logging.level"),
    ("sites: {pornhub: {search_url: 'http://x/'}}", "{query}"),
    ("scheduler: {auto_queue: [{top_k: 1}]}", "query is required"),
    ("paths: [1, 2]", "expected a mapping"),
])
def test_invalid_configs_rejected(tmp_path, body, needle):
    with pytest.raises(ConfigError, match=needle.replace("{", r"\{").replace("}", r"\}")):
        load_config(write(tmp_path, body))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_bad_yaml(tmp_path):
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(write(tmp_path, "a: [unclosed"))
