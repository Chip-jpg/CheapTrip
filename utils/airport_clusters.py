"""Airport cluster expansion utilities."""
from __future__ import annotations

from typing import List, Optional, Set

from config import AIRPORT_CLUSTERS, CLUSTER_REVERSE


def expand_to_cluster(airport: str) -> List[str]:
    """
    Return all airports in the same cluster as `airport`.
    Returns [airport] if it belongs to no cluster.
    """
    cluster_name = CLUSTER_REVERSE.get(airport.upper())
    if cluster_name:
        return list(AIRPORT_CLUSTERS[cluster_name])
    return [airport.upper()]


def expand_list_to_clusters(airports: List[str]) -> List[str]:
    """
    Expand a list of airports so each is replaced by its full cluster.
    Deduplicates the result while preserving rough order.
    """
    seen: Set[str] = set()
    result: List[str] = []
    for ap in airports:
        for member in expand_to_cluster(ap):
            if member not in seen:
                seen.add(member)
                result.append(member)
    return result


def get_cluster_name(airport: str) -> Optional[str]:
    """Return the cluster city name for an airport, or None."""
    return CLUSTER_REVERSE.get(airport.upper())


def cluster_display_name(airport: str) -> str:
    """Return city cluster name if available, else the airport code itself."""
    return CLUSTER_REVERSE.get(airport.upper(), airport.upper())


def are_in_same_cluster(a: str, b: str) -> bool:
    cluster_a = CLUSTER_REVERSE.get(a.upper())
    cluster_b = CLUSTER_REVERSE.get(b.upper())
    return cluster_a is not None and cluster_a == cluster_b
