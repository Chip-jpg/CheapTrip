"""Tests for airport cluster expansion logic."""
from __future__ import annotations

from utils.airport_clusters import (
    are_in_same_cluster,
    cluster_display_name,
    expand_list_to_clusters,
    expand_to_cluster,
    get_cluster_name,
)


def test_expand_to_cluster_milan():
    result = expand_to_cluster("MXP")
    assert set(result) == {"MXP", "LIN", "BGY"}


def test_expand_to_cluster_london():
    result = expand_to_cluster("LGW")
    assert "LHR" in result
    assert "STN" in result
    assert "LTN" in result
    assert "LGW" in result


def test_expand_to_cluster_unknown_airport():
    result = expand_to_cluster("XYZ")
    assert result == ["XYZ"]


def test_expand_list_to_clusters_deduplicates():
    # MXP and BGY are both Milan — result should not have duplicates
    result = expand_list_to_clusters(["MXP", "BGY"])
    assert result.count("MXP") == 1
    assert result.count("BGY") == 1
    assert result.count("LIN") == 1


def test_expand_list_preserves_non_cluster():
    result = expand_list_to_clusters(["WAW"])
    assert "WAW" in result


def test_get_cluster_name():
    assert get_cluster_name("MXP") == "Milan"
    assert get_cluster_name("CDG") == "Paris"
    assert get_cluster_name("XYZ") is None


def test_cluster_display_name_in_cluster():
    assert cluster_display_name("LIN") == "Milan"
    assert cluster_display_name("ORY") == "Paris"


def test_cluster_display_name_standalone():
    assert cluster_display_name("KRK") == "KRK"


def test_are_in_same_cluster_true():
    assert are_in_same_cluster("MXP", "BGY") is True
    assert are_in_same_cluster("LHR", "STN") is True


def test_are_in_same_cluster_false():
    assert are_in_same_cluster("MXP", "LHR") is False
    assert are_in_same_cluster("CDG", "WAW") is False


def test_are_in_same_cluster_same_airport():
    assert are_in_same_cluster("MXP", "MXP") is True


def test_expand_list_order_preserves_first_occurrence():
    result = expand_list_to_clusters(["CDG", "ORY"])
    # Both are Paris — should appear once each
    assert result.count("CDG") == 1
    assert result.count("ORY") == 1
