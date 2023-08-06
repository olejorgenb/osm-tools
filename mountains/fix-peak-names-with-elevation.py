#!/usr/bin/env python3

import json
import re
import sys
import urllib
import urllib.request, urllib.error
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element

import logging

logger = logging.getLogger()

name_with_elevation_pattern = re.compile(r"([^0-9]*)\s+([0-9]+)(.*)")
only_elevation_pattern = re.compile(r"([0-9]+)\s*(.*)")


def get_elevation(coords: list[tuple[float, float]], epsg_code=4326) -> dict[tuple[float, float], float]:
    """
    Based on n50osm.py

    :param coords: list of lon lat tuples
    """

    header = {"User-Agent": "osm-fix-peak-names-with-elevation"}

    elevations = {}

    count_missing = 0
    count_total = len(coords)

    pending_coords = coords[:]

    for endpoint in ["datakilder/dtm1/punkt", "punkt"]:

        endpoint_list = pending_coords[:]
        pending_coords = []

        for i in range(0, len(endpoint_list), 50):
            logger.info("\r\t%i " % ((len(endpoint_list) - i) // 50 * 50))

            nodes_string = json.dumps(endpoint_list[i: i + 50]).replace(" ", "")
            url = f"https://ws.geonorge.no/hoydedata/v1/{endpoint}?punkter={nodes_string}&geojson=false&koordsys={epsg_code}"
            request = urllib.request.Request(url, headers=header)

            try:
                file = urllib.request.urlopen(request)
            except urllib.error.HTTPError as err:
                logger.info("\r\t\t*** %s\n" % err)
                raise

            result = json.load(file)
            file.close()

            for node in result['punkter']:
                point = (node['x'], node['y'])
                if node['z'] is not None:
                    logger.info(f"Found elevation using endpoint: {endpoint}")
                    elevations[point] = node['z']  # Store for possible identical request later
                    # if "datakilde" not in node and "dtm1" in endpoint:
                    #     node['datakilde'] = "dtm1"
                    # create_point(point, {'ele': '%.2f %s' % (node['z'], node['datakilde'])})

                elif endpoint == "punkt":  # Last pass
                    count_missing += 1
                    elevations[point] = None  # Some coastline points + Areas in North with missing DTM
                    # #				logger.info(" *** NO DTM ELEVATION: %s \n" % str(node))
                    # create_point(point, {'ele': 'Missing'})  # , object_type = "DTM")
                else:
                    pending_coords.append(point)  # One more try in next pass


    if count_missing == count_total and count_total > 10:
        logger.info("\r\t*** NO ELEVATIONS FOUND - Perhaps API is currently down\n")
    elif count_missing > 0:
        logger.info("\r\t%i elevations not found\n" % count_missing)

    return elevations


def get_coord(node_element: Element) -> tuple[float, float]:
    # return float(node_element.get("lat")), float(node_element.get("lon"))
    return float(node_element.get("lon")), float(node_element.get("lat"))


def mk_tag_element(name: str, value: str | int | float) -> Element:
    if isinstance(value, int | float):
        value = str(value)
    tag_element = ET.Element('tag')
    tag_element.set("k", name)
    tag_element.set("v", value)
    return tag_element


def main():
    """
    Detect peak names which are likely to contain the elevation.
    Output osm xml suitable for correcting these errors. Cross-check with Kartverket DTM (so only suitable for Norway atm.)

    Intended to be used on OSM files only containing peak nodes (but have some sanity checks)

    Overpass query:

        [out:xml][timeout:90][bbox:{{bbox}}];
        (
          nwr["natural"="peak"];
        );
        (._;>;);
        out meta;

    Remember to clean up helper tags before uploading!
    """
    file = sys.stdin

    tree = ET.parse(file)
    root = tree.getroot()

    for node_element in root:
        name_tag: Element | None  = None
        ele_tag: Element | None = None
        natural_tag: Element | None = None
        for tag_element in node_element:
            if tag_element.tag != "tag":
                continue
            k = tag_element.get("k")
            if k == "name":
                name_tag = tag_element
            elif k == "ele":
                ele_tag = tag_element
            elif k == "natural":
                natural_tag = tag_element

        if natural_tag is None or natural_tag.get("v") not in ("peak", "hill"):
            continue

        if name_tag is None:
            continue

        name = name_tag.get("v")
        assert name is not None

        m = name_with_elevation_pattern.fullmatch(name)
        if m:
            name_without_ele = m.group(1)
            ele = m.group(2)
            rest = m.group(3)
        else:
            m = only_elevation_pattern.fullmatch(name)
            if not m:
                continue

            name_without_ele = ""
            ele = m.group(1)
            rest = m.group(2)

        existing_ele = ele_tag.get("v") if ele_tag else ""

        if len(rest) > 0:
            logger.warning(f"Skipping {name}")
            continue

        coord = get_coord(node_element)
        dmt_ele = get_elevation([coord])[coord]

        if abs(dmt_ele - float(ele)) > 20:
            logger.warning(f"Skipping {name} ({dmt_ele=})")
            continue

        if existing_ele and abs(float(ele) - float(existing_ele)) > 10:
            logger.warning(f"Skipping {name} ({existing_ele=})")
            continue

        name_tag.set("v", name_without_ele)
        node_element.append(mk_tag_element("ele", str(ele)))
        node_element.append(mk_tag_element("NOTE", "fixed name with elevation"))
        node_element.append(mk_tag_element("name:original", name))
        node_element.append(mk_tag_element("ele:kartverket-dmt", dmt_ele))

    output_xml = ET.tostring(root, encoding="unicode")
    print(output_xml)


if __name__ == '__main__':
    main()
