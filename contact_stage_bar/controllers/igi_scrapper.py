import requests
from bs4 import BeautifulSoup

def fetch_igi_report_data(report_number):
    url = f"https://www.igi.org/verify-your-report/?r={report_number}"
    headers = {
        "User-Agent": "Mozilla/5.0"  # Fools basic bot protections
    }
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return {"error": "Failed to fetch report"}

    soup = BeautifulSoup(response.content, "html.parser")
    
    # Example: Extracting the Report Number, Shape, Carat, etc.
    data = {}
    try:
        # This part is hypothetical: adjust selectors based on real HTML structure
        data["report_number"] = report_number
        data["shape"] = soup.select_one(".report-shape").text.strip()
        data["carat"] = soup.select_one(".report-carat").text.strip()
        data["cut"] = soup.select_one(".report-cut").text.strip()
        data["color"] = soup.select_one(".report-color").text.strip()
        data["clarity"] = soup.select_one(".report-clarity").text.strip()
    except Exception as e:
        data["error"] = f"Failed to parse report: {str(e)}"

    return data
