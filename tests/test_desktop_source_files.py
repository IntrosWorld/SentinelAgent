import pytest

from sentinel_agent.desktop_documents import DesktopDocuments, TEXT_EXTENSIONS, is_document_goal


@pytest.mark.parametrize("extension", sorted(TEXT_EXTENSIONS))
def test_real_source_file_roundtrip(tmp_path, extension):
    documents = DesktopDocuments(tmp_path)
    filename = "sample" + extension
    content = '<h1>Hello 🌍</h1>\nconst value = "create report.pdf";\n'
    assert is_document_goal("create " + filename)
    action = documents.plan("create " + filename + " with content " + content)
    documents.prepare(action)
    documents.execute(action, None)
    target = documents.path(filename)
    assert target.read_bytes() == content.encode("utf-8")
    action = documents.plan("append " + filename + " with content More")
    result = documents.execute(action, documents.fingerprint(target))
    assert target.read_text(encoding="utf-8") == content + "More"
    assert result["backup_path"]


def test_empty_file_and_changed_file_rejection(tmp_path):
    documents = DesktopDocuments(tmp_path)
    action = documents.plan("create empty.txt")
    documents.execute(action, None)
    target = documents.path("empty.txt")
    fingerprint = documents.fingerprint(target)
    target.write_text("external edit")
    with pytest.raises(ValueError, match="changed"):
        documents.execute(action, fingerprint)


@pytest.mark.parametrize("name", ["../bad.js", "C:\\bad.txt", "NUL.txt", "file:stream.js", "bad.exe", "bad.bat", "bad.txt."])
def test_unsafe_source_filename_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        DesktopDocuments(tmp_path).path(name)
