from worlds.generic.Rules import set_rule
from .Regions import regionMap
from worlds.AutoWorld import World
from BaseClasses import MultiWorld
from .Helpers import clamp, is_item_enabled
import re
import math


def infix_to_postfix(expr, location):
    prec = {"&": 2, "|": 2, "!": 3}

    stack = []
    postfix = ""

    try:
        for c in expr:
            if c.isnumeric():
                postfix += c
            elif c in prec:
                while stack and stack[-1] != "(" and prec[c] <= prec[stack[-1]]:
                    postfix += stack.pop()
                stack.append(c)
            elif c == "(":
                stack.append(c)
            elif c == ")":
                while stack and stack[-1] != "(":
                    postfix += stack.pop()
                stack.pop()
        while stack:
            postfix += stack.pop()
    except Exception:
        raise KeyError("Invalid logic format for location/region {}.".format(location))
    return postfix


def evaluate_postfix(expr, location):
    stack = []
    try:
        for c in expr:
            if c == "0":
                stack.append(False)
            elif c == "1":
                stack.append(True)
            elif c == "&":
                op2 = stack.pop()
                op1 = stack.pop()
                stack.append(op1 and op2)
            elif c == "|":
                op2 = stack.pop()
                op1 = stack.pop()
                stack.append(op1 or op2)
            elif c == "!":
                op = stack.pop()
                stack.append(not op)
    except Exception:
        raise KeyError("Invalid logic format for location/region {}.".format(location))

    if len(stack) != 1:
        raise KeyError("Invalid logic format for location/region {}.".format(location))
    return stack.pop()


def set_rules(base: World, world: MultiWorld, player: int):
    # this is only called if a player's item_count doesnt exist
    def get_fallback_item_counts():
        if 'fallback' not in base.item_counts:
            base.item_counts['fallback'] = {}
            for item in base.item_name_to_item.values():
                if is_item_enabled(world,player,item):
                    base.item_counts['fallback'][item['name']] = int(item.get('count', 1))

        return base.item_counts.get('fallback')

    # this is only called when the area (think, location or region) has a "requires" field that is a string
    def checkRequireStringForArea(state, area):
        requires_list = area["requires"]
        # fallback if items_counts[player] not present (will not be accurate to hooks item count)
        items_counts = base.item_counts.get(player, get_fallback_item_counts())

        # parse user written statement into list of each item
        for item in re.findall(r'\|[^|]+\|', area["requires"]):
            require_type = 'item'

            if '|@' in item:
                require_type = 'category'

            item_base = item
            item = item.replace('|', '').replace('@', '')

            item_parts = item.split(":")
            item_name = item
            item_count = "1"
            if len(item_parts) > 1:
                item_name = item_parts[0]
                item_count = item_parts[1]

            total = 0

            if require_type == 'category':
                category_items = [item for item in base.item_name_to_item.values() if "category" in item and item_name in item["category"]]
                category_items_counts = sum([items_counts.get(category_item["name"], 0) for category_item in category_items])
                if item_count.lower() == 'all':
                    item_count = category_items_counts
                elif item_count.lower() == 'half':
                    item_count = category_items_counts / 2
                elif item_count.endswith('%') and len(item_count) > 1:
                    percent = clamp(float(item_count[:-1]) / 100, 0, 1)
                    item_count = math.ceil(category_items_counts * percent)
                else:
                    item_count = int(item_count)

                for category_item in category_items:
                    total += state.count(category_item["name"], player)

                    if total >= item_count:
                        requires_list = requires_list.replace(item_base, "1")
            elif require_type == 'item':
                item_current_count = items_counts.get(item_name, 0)
                if item_count.lower() == 'all':
                    item_count = item_current_count
                elif item_count.lower() == 'half':
                    item_count = item_current_count / 2
                elif item_count.endswith('%') and len(item_count) > 1:
                    percent = clamp(float(item_count[:-1]) / 100, 0, 1)
                    item_count = math.ceil(item_current_count * percent)
                else:
                    item_count = int(item_count)

                total = state.count(item_name, player)

                if total >= item_count:
                    requires_list = requires_list.replace(item_base, "1")

            if total <= item_count:
                requires_list = requires_list.replace(item_base, "0")

        requires_list = re.sub(r'\s?\bAND\b\s?', '&', requires_list, 0, re.IGNORECASE)
        requires_list = re.sub(r'\s?\bOR\b\s?', '|', requires_list, 0, re.IGNORECASE)

        requires_string = infix_to_postfix("".join(requires_list), area)
        return (evaluate_postfix(requires_string, area))

    # this is only called when the area (think, location or region) has a "requires" field that is a dict
    def checkRequireDictForArea(state, area):
        canAccess = True

        for item in area["requires"]:
            # if the require entry is an object with "or" or a list of items, treat it as a standalone require of its own
            if (isinstance(item, dict) and "or" in item and isinstance(item["or"], list)) or (isinstance(item, list)):
                canAccessOr = True
                or_items = item

                if isinstance(item, dict):
                    or_items = item["or"]

                for or_item in or_items:
                    or_item_parts = or_item.split(":")
                    or_item_name = or_item
                    or_item_count = 1

                    if len(or_item_parts) > 1:
                        or_item_name = or_item_parts[0]
                        or_item_count = int(or_item_parts[1])

                    if not state.has(or_item_name, player, or_item_count):
                        canAccessOr = False

                if canAccessOr:
                    canAccess = True
                    break
            else:
                item_parts = item.split(":")
                item_name = item
                item_count = 1

                if len(item_parts) > 1:
                    item_name = item_parts[0]
                    item_count = int(item_parts[1])

                if not state.has(item_name, player, item_count):
                    canAccess = False

        return canAccess

    # handle any type of checking needed, then ferry the check off to a dedicated method for that check
    def fullLocationOrRegionCheck(state, area):
        # if it's not a usable object of some sort, default to true
        if not area:
            return True

        # don't require the "requires" key for locations and regions if they don't need to use it
        if "requires" not in area.keys():
            return True

        if isinstance(area["requires"], str):
            return checkRequireStringForArea(state, area)
        else:  # item access is in dict form
            return checkRequireDictForArea(state, area)

    used_location_names = []
    # Region access rules
    for region in regionMap.keys():
        used_location_names.extend([l.name for l in world.get_region(region, player).locations])
        if region != "Menu":
            for exitRegion in world.get_region(region, player).exits:
                def fullRegionCheck(state, region=regionMap[region]):
                    return fullLocationOrRegionCheck(state, region)

                set_rule(world.get_entrance(exitRegion.name, player), fullRegionCheck)

    # Location access rules
    for location in base.location_table:
        if location["name"] not in used_location_names:
            continue

        locFromWorld = world.get_location(location["name"], player)

        locationRegion = regionMap[location["region"]] if "region" in location else None

        if "requires" in location: # Location has requires, check them alongside the region requires
            def checkBothLocationAndRegion(state, location=location, region=locationRegion):
                locationCheck = fullLocationOrRegionCheck(state, location)
                regionCheck = True # default to true unless there's a region with requires

                if region:
                    regionCheck = fullLocationOrRegionCheck(state, region)

                return locationCheck and regionCheck

            set_rule(locFromWorld, checkBothLocationAndRegion)
        elif "region" in location: # Only region access required, check the location's region's requires
            def fullRegionCheck(state, region=locationRegion):
                return fullLocationOrRegionCheck(state, region)

            set_rule(locFromWorld, fullRegionCheck)
        else: # No location region and no location requires? It's accessible.
            def allRegionsAccessible(state):
                return True

            set_rule(locFromWorld, allRegionsAccessible)

    # Victory requirement
    world.completion_condition[player] = lambda state: state.has("__Victory__", player)
