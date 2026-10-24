"""Tests for pyGCxGC.ms.structures (no network: PubChem is stubbed)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest
from synthetic import lib_from_dict

from pyGCxGC.ms.structures import (
    PubChem,
    Structure,
    StructureCache,
    depict_svg,
    from_entry,
    is_resolved,
    lookup_keys,
    rdkit_available,
    resolve,
)

DECANE = {
    "CID": 15600,
    "SMILES": "CCCCCCCCCC",
    "InChIKey": "DIOQZVSQGTUSAI-UHFFFAOYSA-N",
}


class FakePubChem:
    """``opener`` for :class:`PubChem`: answers from ``known`` (CAS/name), else 404."""

    def __init__(self, known: dict[str, dict]):
        self.known = known
        self.calls: list[tuple[str, bytes | None]] = []

    def __call__(self, url, data, timeout):
        assert timeout == 10
        self.calls.append((url, data))
        value = data.decode().partition("=")[2] if data else url.split("/")[-4]
        value = urllib.parse.unquote_plus(value)
        if value not in self.known:
            raise urllib.error.HTTPError(url, 404, "Not found", {}, io.BytesIO())
        return json.dumps(
            {"PropertyTable": {"Properties": [self.known[value]]}}
        ).encode()


def pubchem(known):
    opener = FakePubChem(known)
    client = PubChem(opener=opener)
    client.min_interval = 0
    return client, opener


def test_structure_from_library_entry():
    lib = lib_from_dict()
    toluene = from_entry(lib.entry(0), "test")
    assert toluene.smiles == "Cc1ccccc1"
    assert toluene.key == "YXFVVABEGXRONW-UHFFFAOYSA-N"
    assert toluene.source == "test"
    # without an InChIKey the key is a hash of the SMILES
    naphthalene = from_entry(lib.entry(1))
    assert naphthalene.key.startswith("h")
    assert naphthalene.key == Structure(smiles="c1ccc2ccccc2c1").key
    assert from_entry(lib.entry(2)) is None
    assert lookup_keys(lib.entry(2)) == [("cas", "124-18-5"), ("name", "Decane")]


def test_resolve_online_and_cache(tmp_path):
    lib = lib_from_dict()
    decane = lib.entry(2)
    cache = StructureCache(tmp_path / "structures.sqlite")
    client, opener = pubchem({"124-18-5": DECANE})

    # offline: nothing is sent
    assert resolve(decane, cache=cache, pubchem=client) is None
    assert opener.calls == []
    assert not is_resolved(decane, cache)

    found = resolve(decane, online=True, cache=cache, pubchem=client)
    assert found.smiles == "CCCCCCCCCC"
    assert found.pubchem_cid == 15600
    assert found.source == "PubChem"
    # CAS lookups are POSTed by name, only the identifier is sent
    assert opener.calls == [
        (
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/property/"
            "SMILES,InChI,InChIKey/JSON",
            b"name=124-18-5",
        )
    ]
    assert is_resolved(decane, cache)

    # the cache answers from now on, also after reopening
    cache.close()
    cache = StructureCache(tmp_path / "structures.sqlite")
    assert resolve(decane, online=True, cache=cache, pubchem=client) == found
    assert len(opener.calls) == 1
    assert cache.get(found.key) == found

    # "not found" is cached too: CAS, then name, then never again
    heavy = lib.entry(3)
    assert resolve(heavy, online=True, cache=cache, pubchem=client) is None
    assert [c[1] for c in opener.calls[1:]] == [b"name=Heavy"]
    assert resolve(heavy, online=True, cache=cache, pubchem=client) is None
    assert len(opener.calls) == 2
    assert is_resolved(heavy, cache)


def test_resolve_falls_back_from_cas_to_name():
    lib = lib_from_dict()
    client, opener = pubchem({"Decane": DECANE})
    found = resolve(lib.entry(2), online=True, cache=StructureCache(), pubchem=client)
    assert found.inchikey == DECANE["InChIKey"]
    assert [c[1] for c in opener.calls] == [b"name=124-18-5", b"name=Decane"]


def test_pubchem_errors():
    def failing(url, data, timeout):
        raise urllib.error.HTTPError(url, 503, "Busy", {}, io.BytesIO())

    with pytest.raises(OSError, match="HTTP 503"):
        PubChem(opener=failing).lookup("cas", "124-18-5")

    def unreachable(url, data, timeout):
        raise urllib.error.URLError("no network")

    with pytest.raises(OSError, match="unreachable"):
        PubChem(opener=unreachable).lookup("name", "decane")
    # old property names are understood
    old = {"CID": 1, "IsomericSMILES": "C", "InChIKey": "X"}
    client, _ = pubchem({"methane": old})
    assert client.lookup("name", "methane").smiles == "C"


@pytest.mark.skipif(not rdkit_available(), reason="RDKit is not installed")
def test_depict_svg():
    toluene = Structure(smiles="Cc1ccccc1")
    light = depict_svg(toluene, 200, 150)
    dark = depict_svg(toluene, 200, 150, dark=True)
    assert light.startswith("<svg")
    assert "width='200px'" in light
    assert light != dark
    assert "<rect" not in light  # transparent background
    assert depict_svg(Structure(inchi="InChI=1S/CH4/h1H4")).startswith("<svg")
    with pytest.raises(ValueError, match="cannot be parsed"):
        depict_svg(Structure(smiles="not a smiles"))
