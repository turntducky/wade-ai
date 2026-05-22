import re
import json
import pycountry
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from typing import Optional

from app.skills.registry import register_tool

COUNTRY_DATA = {}
for country in pycountry.countries:
    iso3 = country.alpha_3
    base_name = getattr(country, 'common_name', country.name).lower()    
    patterns = [r"\b" + re.escape(base_name) + r"\b"]
    
    COUNTRY_DATA[iso3] = {
        "iso": iso3,
        "region": base_name.upper(), 
        "name": getattr(country, 'common_name', country.name),
        "patterns": [re.compile(p) for p in patterns]
    }

CUSTOM_ALIASES = {
    "USA": [r"\bus\b", r"\bu\.s\.", r"\bunited states\b", r"\bvirginia\b", r"\blouisiana\b", r"\bsan francisco\b", r"\bnew york\b", r"\bcoachella\b", r"\btrump\b", r"\bobama\b"],
    "RUS": [r"\bmoscow\b", r"\bputin\b", r"\brussian\b", r"\brussians\b"],
    "UKR": [r"\bkyiv\b", r"\bzelensky\b", r"\bukrainian\b", r"\bukrainians\b"],
    "GBR": [r"\buk\b", r"\bbritain\b", r"\blondon\b", r"\bengland\b", r"\bbritish\b", r"\bscottish\b", r"\bwelsh\b"],
    "CHN": [r"\bbeijing\b", r"\bxi jinping\b", r"\bchinese\b"],
    "ISR": [r"\bjerusalem\b", r"\btel aviv\b", r"\bisraeli\b", r"\bisraelis\b"],
    "PSE": [r"\bgaza\b", r"\bpalestine\b", r"\bhamas\b", r"\bpalestinian\b", r"\bpalestinians\b"],
    "JPN": [r"\btokyo\b", r"\bjapanese\b"],
    "IND": [r"\bdelhi\b", r"\bindian\b"],
    "BRA": [r"\bbrazilian\b", r"\bbrazilians\b", r"\brio de janeiro\b"],
    "TUR": [r"\bturkish\b", r"\bankara\b", r"\bistanbul\b"],
    "ITA": [r"\bnaples\b", r"\brome\b", r"\bitalian\b"],
    "ZAF": [r"\bsouth african\b"],
    "FRA": [r"\bparis\b", r"\bparisians?\b", r"\bfrench\b", r"\bmacron\b"],
    "IRN": [r"\btehran\b", r"\biranian\b", r"\biranians\b"],
    "CAN": [r"\bontario\b", r"\bcanadian\b", r"\bcanadians\b", r"\btoronto\b", r"\btrudeau\b"],
    "BGR": [r"\bbulgarian?\b", r"\bsofia\b"],
    "TTO": [r"\btrinidad\b", r"\btobago\b"],
    "SDN": [r"\bsudan\b", r"\bsudanese\b"],
    "KOR": [r"\bsouth korea\b", r"\bsouth korean\b", r"\bseoul\b"],
    "PRK": [r"\bnorth korea\b", r"\bnorth korean\b", r"\bpyongyang\b"],
    "MYS": [r"\bmalaysia\b", r"\bmalaysian\b"],
    "LBN": [r"\blebanon\b", r"\blebanese\b", r"\bbeirut\b"],
    "DEU": [r"\bgerman\b", r"\bgermany\b", r"\bberlin\b"]
}

for iso, aliases in CUSTOM_ALIASES.items():
    if iso in COUNTRY_DATA:
        COUNTRY_DATA[iso]["patterns"].extend([re.compile(a) for a in aliases])

def _guess_coordinates(text: str) -> dict:
    """A globally scaled geo-tagger utilizing pre-compiled regex for speed."""
    text_lower = text.lower()
    
    for iso, data in COUNTRY_DATA.items():
        for pattern in data["patterns"]:
            if pattern.search(text_lower):
                return {"iso": data["iso"], "region": data["region"]}
                
    return {"iso": None, "region": "GLOBAL"}

def _determine_urgency(title: str) -> str:
    """Assigns W.A.D.E. tactical colors based on keywords."""
    alert_words = ["war", "strike", "attack", "crisis", "crash", "dead", "threat", "emergency"]
    caution_words = ["warns", "tension", "protest", "investigation", "drop", "sanction"]
    
    title_lower = title.lower()
    if any(w in title_lower for w in alert_words): return "alertred"
    if any(w in title_lower for w in caution_words): return "cautiongold"
    return "sonargreen"

def _calculate_volatility(intel_feed: list) -> float:
    """Calculates the global volatility index based on tactical alert colors."""
    base_index = 35.0
    
    for item in intel_feed:
        if item["color"] == "alertred":
            base_index += 12.5      # Major spike for critical events
        elif item["color"] == "cautiongold":
            base_index += 5.0       # Moderate increase for warnings
        elif item["color"] == "sonargreen":
            base_index -= 1.5       # Slight decrease for stable/neutral news
            
    final_index = max(0.0, min(100.0, base_index))
    
    return round(final_index, 1)

def _generate_wade_insight(volatility: float, intel_feed: list) -> str:
    """Generates a dynamic W.A.D.E. analysis string based on the volatility score and feed data."""
    high_risk = [item["region"] for item in intel_feed if item["color"] == "alertred" and item["region"] != "GLOBAL"]
    unique_regions = list(set(high_risk))
    
    region_text = ""
    if unique_regions:
        region_text = f" Concentrated instability detected in {', '.join(unique_regions[:2])}."
        
    if volatility >= 80.0:
        return f"CRITICAL THRESHOLD EXCEEDED. Multiple severe intercepts detected across the grid.{region_text} Recommending heightened situational awareness."
    elif volatility >= 50.0:
        return f"Elevated geopolitical tension detected. Monitoring developing situations.{region_text} Baseline variance is above nominal parameters."
    else:
        return "Global telemetry is currently within nominal operational bounds. Routine market and geopolitical variance detected. No critical anomalies present."

@register_tool("get_global_recon_intel")
async def execute_get_global_recon_intel(scope: str = "global", country_iso: Optional[str] = None) -> str:
    intel_feed = []
    
    try:
        if scope == "global":
            feed_url = "http://feeds.bbci.co.uk/news/world/rss.xml"
        elif scope == "country" and country_iso:
            iso_str: str = country_iso.upper()
            c_obj = pycountry.countries.get(alpha_3=iso_str)
            country_name = getattr(c_obj, 'common_name', c_obj.name) if c_obj else iso_str
            
            safe_query = urllib.parse.quote(f"{country_name} news")
            feed_url = f"https://news.google.com/rss/search?q={safe_query}&hl=en-US&gl=US&ceid=US:en"
        else:
            return json.dumps({"status": "error", "message": "Invalid scope parameters."})

        req = urllib.request.Request(feed_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            xml_data = response.read()
            
        root = ET.fromstring(xml_data)
        items = root.findall('./channel/item')
        
        for item in items[:50]:
            title_el = item.find('title')
            title: str = str(title_el.text) if title_el is not None and title_el.text else "Unknown Signal"
            pub_date_el = item.find('pubDate')
            pub_date: str = str(pub_date_el.text) if pub_date_el is not None and pub_date_el.text else "RECENT"
            time_str = pub_date.split(',')[1].strip()[:16] if ',' in pub_date else pub_date[:16]
            
            if scope == "country" and country_iso:
                iso_str: str = country_iso.upper()
                c_obj = pycountry.countries.get(alpha_3=iso_str)
                geo = {"iso": iso_str, "region": getattr(c_obj, 'common_name', c_obj.name).upper() if c_obj else "LOCAL"}
            else:
                geo = _guess_coordinates(title)
                
            color = _determine_urgency(title)
            
            intel_feed.append({
                "iso": geo["iso"],
                "region": geo["region"],
                "time": time_str,
                "color": color,
                "story": title
            })
            
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

    dynamic_volatility = _calculate_volatility(intel_feed)
    insight_text = _generate_wade_insight(dynamic_volatility, intel_feed)

    return json.dumps({
        "status": "secure_uplink_established",
        "intel_feed": intel_feed,
        "wade_volatility_index": dynamic_volatility,
        "wade_insight": insight_text
    })