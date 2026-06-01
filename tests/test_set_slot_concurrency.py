import threading
import time

from semantic_server import slots


def test_set_slot_concurrent_writes_preserve_both(tmp_path, monkeypatch):
    memory_dir = str(tmp_path)
    orig_write = slots._write

    def slow_write(md, data):
        # why: widen the read-modify-write window so an unguarded race loses one.
        time.sleep(0.2)
        orig_write(md, data)

    monkeypatch.setattr(slots, "_write", slow_write)

    t1 = threading.Thread(
        target=slots.set_slot, args=(memory_dir, "persona", "P"))
    t2 = threading.Thread(
        target=slots.set_slot, args=(memory_dir, "preferences", "Q"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    result = slots.list_slots(memory_dir)
    assert result["persona"] == "P"
    assert result["preferences"] == "Q"
