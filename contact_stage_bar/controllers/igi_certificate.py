# from odoo import http
# from odoo.http import request
# import requests
# from PyPDF2 import PdfReader
# from io import BytesIO
# import json
# from bs4 import BeautifulSoup



# class IGIPDFController(http.Controller):

        
#     # @http.route('/igi/download/<string:sdk_certificate>', type='http', auth='public')
#     # def download_igi_pdf(self, sdk_certificate):
#     #     url = f"https://www.igi.org/verify-your-report/?r={sdk_certificate}"
#     #     return request.redirect(url)
    
#     @http.route('/igi/fetch_report', type='json', auth='public', methods=['POST'], csrf=False)
#     def fetch_report(self, **kwargs):
#         print(kwargs, "kwargs")
#         report_no = kwargs.get('Report number')
#         if not report_no:
#             return {"error": "Missing report_no"}

#         url = f'https://www.igi.org/verify-your-report/?r={report_no}'
#         headers = {'User-Agent': 'Mozilla/5.0'}
#         resp = requests.get(url, headers=headers)
        
#         if resp.status_code != 200:
#             return {"error": "IGI returned status " + str(resp.status_code)}
        
#         soup = BeautifulSoup(resp.text, 'html.parser')
#         # Examples: adjust selectors based on real HTML structure
#         summary = soup.select_one('.summary-no').get_text(strip=True)
#         carat   = soup.select_one('.total-est-wt').get_text(strip=True)
#         color   = soup.select_one('.color').get_text(strip=True)
#         clarity = soup.select_one('.clarity').get_text(strip=True)

# from odoo import http
# from odoo.http import Response
# import requests ,json
# from bs4 import BeautifulSoup
# from odoo.http import request


# class IGIVerifyController(http.Controller):

    
#     @http.route('/igi/fetch_report', type='json', auth='public', methods=['POST'], csrf=False)
#     def fetch_report(self):
#         data = request.jsonrequest  # ✅ Will now work
#         print("DATA RECEIVED:", data)

#         report_no = data.get('report_no')
#         if not report_no:
#             return {"error": "Missing report_no"}

#         url = f'https://www.igi.org/verify-your-report/?r={report_no}'
#         headers = {'User-Agent': 'Mozilla/5.0'}
#         resp = requests.get(url, headers=headers)

#         if resp.status_code != 200:
#             return {"error": f"Failed to fetch report. Status {resp.status_code}"}

#         soup = BeautifulSoup(resp.text, 'html.parser')

#         # Find based on visible text
#         def extract_by_label(label):
#             label_el = soup.find(text=lambda t: t and label in t)
#             if label_el and label_el.parent and label_el.parent.find_next_sibling():
#                 return label_el.parent.find_next_sibling().get_text(strip=True)
#             return None

#         data = {
#             "report_number": extract_by_label("Report number"),
#             "issue_date": extract_by_label("Issue Date"),
#             "description": extract_by_label("Description"),
#             "shape_cut": extract_by_label("Shape and Cutting Style"),
#             "measurements": extract_by_label("Measurements"),
#             "carat_weight": extract_by_label("Carat Weight"),
#             "color_grade": extract_by_label("Color Grade"),
#             "clarity_grade": extract_by_label("Clarity Grade"),
#             "cut_grade": extract_by_label("Cut Grade"),
#             "polish": extract_by_label("Polish"),
#             "symmetry": extract_by_label("Symmetry"),
#             "fluorescence": extract_by_label("Fluorescence"),
#             "inscription": extract_by_label("Inscription"),
#         }
#         print(data, "data")

#         return {"status": "success", "data": data}

from odoo import http
from odoo.http import request
import requests
from bs4 import BeautifulSoup
import json
# from custom_addons.contact_stage_bar.controllers.igi_scraper import fetch_igi_report_data


# class IGIVerifyController(http.Controller):

#     @http.route('/api/igi/report', type='json', auth='public', methods=['POST'], csrf=False)
#     def get_igi_report(self, **post):
#         report_number = post.get('report_number')
#         if not report_number:
#             return {"error": "Missing report_number in payload"}

#         # data = fetch_igi_report_data(report_number)
#         return data


    # @http.route('/igi/fetch_report', type='http', auth='public', methods=['POST'], csrf=False)
    # def fetch_report(self, **kwargs):
    #     try:
    #         # Parse JSON body from the raw HTTP request
    #         raw_data = request.httprequest.data
    #         print("RAW DATA RECEIVED:", raw_data)
    #         data = json.loads(raw_data)
    #         print("PARSED DATA:", data)
    #         report_no = data.get('report_no')
    #         if not report_no:
    #             return request.make_response(
    #                 json.dumps({"error": "Missing report_no"}),
    #                 headers=[('Content-Type', 'application/json')],
    #                 status=400
    #             )

    #         url = f'https://www.igi.org/verify-your-report/?r={report_no}'
    #         headers = {'User-Agent': 'Mozilla/5.0'}
    #         resp = requests.get(url, headers=headers)

    #         if resp.status_code != 200:
    #             print(f"Failed to fetch report. Status {resp.status_code}")
    #             return request.make_response(
    #                 json.dumps({"error": f"Failed to fetch report. Status {resp.status_code}"}),
    #                 headers=[('Content-Type', 'application/json')],
    #                 status=500
    #             )

    #         soup = BeautifulSoup(resp.text, 'html.parser')

    #         # You would now extract data from soup here...
    #         # For now, we'll just return basic confirmation
    #         return request.make_response(
    #             json.dumps({"status": "success", "report_no": report_no}),
    #             headers=[('Content-Type', 'application/json')],
    #             status=200
    #         )
    #     except Exception as e:
    #         return request.make_response(
    #             json.dumps({"error": str(e)}),
    #             headers=[('Content-Type', 'application/json')],
    #             status=500
    #         )
