import json
import asyncio
import urllib.parse
import urllib.error
import urllib.request

from app.skills.registry import register_tool

@register_tool("get_weather")
async def get_weather(location: str | None = None) -> str:
    """Geocodes a location to precise coordinates, then fetches the weather."""
    try:
        def _fetch():
            target_location = location
            
            if not target_location or target_location.strip() == "":
                try:
                    with urllib.request.urlopen("http://ip-api.com/json/", timeout=5) as ip_response:
                        ip_data = json.loads(ip_response.read().decode())
                        if ip_data.get("status") == "success":
                            target_location = f"{ip_data.get('city')}, {ip_data.get('region')}"
                        else:
                            return "Error: Could not auto-detect location via IP. Please specify a location (e.g. 'Guntersville, AL')."
                except Exception:
                    return "Error: IP Geolocation service is down. Please provide a location explicitly."

            parts = [p.strip() for p in target_location.split(",")]
            city_name = parts[0]
            
            safe_city = urllib.parse.quote(city_name)
            geocode_url = f"https://geocoding-api.open-meteo.com/v1/search?name={safe_city}&count=10&language=en&format=json"
            
            try:
                with urllib.request.urlopen(geocode_url) as response:
                    geo_data = json.loads(response.read().decode())
            except urllib.error.URLError as e:
                return f"Network Error: Could not connect to Geocoding API. {e}"

            if not geo_data.get("results"):
                return f"Error: No results found for '{city_name}'. Please try a more specific city/state name."
                
            best_match = geo_data["results"][0]
            if len(parts) > 1:
                target_region = parts[1].lower()
                for result in geo_data["results"]:
                    regions = [
                        result.get("admin1", "").lower(),
                        result.get("admin1_code", "").lower(),
                        result.get("country", "").lower(),
                        result.get("country_code", "").lower()
                    ]
                    if target_region in regions:
                        best_match = result
                        break
            else:
                try:
                    with urllib.request.urlopen("http://ip-api.com/json/", timeout=4) as ip_resp:
                        ip_data = json.loads(ip_resp.read().decode())
                        if ip_data.get("status") == "success":
                            user_cc = ip_data.get("countryCode", "").lower()
                            for result in geo_data["results"]:
                                if result.get("country_code", "").lower() == user_cc:
                                    best_match = result
                                    break
                except Exception:
                    pass

            lat = best_match["latitude"]
            lon = best_match["longitude"]
            resolved_name = f"{best_match.get('name')}, {best_match.get('admin1', '')} {best_match.get('country_code', '')}".strip(", ")

            weather_url = (
                f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,wind_speed_10m"
                f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                f"&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch&timezone=auto"
            )
            
            with urllib.request.urlopen(weather_url) as response:
                weather_data = json.loads(response.read().decode())

            current = weather_data["current"]
            daily = weather_data["daily"]
            
            output = [
                f"--- Precise Weather for {resolved_name} (Lat: {lat}, Lon: {lon}) ---",
                f"Current Temperature: {current['temperature_2m']}°F (Feels like {current['apparent_temperature']}°F)",
                f"Humidity: {current['relative_humidity_2m']}% | Wind Speed: {current['wind_speed_10m']} mph",
                f"Current Precipitation: {current['precipitation']} inches",
                "",
                "--- Today's Forecast ---",
                f"High: {daily['temperature_2m_max'][0]}°F | Low: {daily['temperature_2m_min'][0]}°F",
                f"Max Precipitation Probability: {daily['precipitation_probability_max'][0]}%"
            ]
            
            return "\n".join(output)
        
        return await asyncio.to_thread(_fetch)
        
    except Exception as e:
        return f"Weather Tool Error: {str(e)}"
    
if __name__ == "__main__":
    async def run_test():
        print("--- TEST 1: No Location Provided (Testing IP Fallback) ---")
        print("Fetching...")
        result_ip = await get_weather()
        print(result_ip)
        
        print("\n\n")
        
        print("--- TEST 2: Specific Location Provided (Testing Override) ---")
        print("Fetching...")
        result_specific = await get_weather("Seattle, WA")
        print(result_specific)

    asyncio.run(run_test())