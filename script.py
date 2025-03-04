import os
import json
import itertools
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Load API keys and CSE configurations from environment variables
api_keys = json.loads(os.getenv("API_KEYS", "[]"))
cse_config = json.loads(os.getenv("CSE_CONFIG", "[]"))

api_key_cycle = itertools.cycle(api_keys)  # Rotate API keys

# ========================= Search Parameters =========================
results_per_page = 10  # Number of results per API call
max_results = 100  # Maximum number of results per CSE query
captured_links = []  # Stores extracted company links
base_url = "https://www.googleapis.com/customsearch/v1"  # Google Custom Search API base URL

# ========================= Function to Fetch Results =========================
def fetch_results(cse_id, query, start_index):
    """
    Fetches search results from the Google Custom Search API using a given CSE ID and query.

    Parameters:
        cse_id (str): The Custom Search Engine ID.
        query (str): The search query.
        start_index (int): The starting index for paginated results.

    Returns:
        dict: JSON response from the API.
    """
    api_key = next(api_key_cycle)  # Get the next available API key
    params = {
        "q": query,
        "cx": cse_id,
        "key": api_key,
        "start": start_index
    }

    response = requests.get(base_url, params=params)
    time.sleep(1)  # Avoid overwhelming the API with requests
    return response.json()

# ========================= Function to Extract Unique Company Names =========================
def get_all_company_names():
    """
    Retrieves and extracts unique company names from Google search results.

    Returns:
        list: A list of unique company names.
    """
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = []

        # Submit search queries in parallel
        for cse_config_item in cse_config:
            cse_id = cse_config_item["cse_id"]
            query = cse_config_item["query"]

            for start_index in range(1, max_results + 1, results_per_page):
                futures.append(executor.submit(fetch_results, cse_id, query, start_index))

        # Process fetched results
        for future in as_completed(futures):
            try:
                result = future.result()

                # Extract links and company names
                for item in result.get("items", []):
                    link = item.get("link", "")

                    if "join.com/companies/" in link:
                        company_name = link.split("join.com/companies/")[-1].split("/")[0]
                        link = f"https://join.com/companies/{company_name}"

                        # Store only unique links
                        if link not in {entry["link"] for entry in captured_links}:
                            captured_links.append({
                                "link": link,
                                "company_name": company_name
                            })
                        print(f"Company: {company_name}")

            except Exception as e:
                print(f"Error: {e}")

    # ========================= Load Existing Data =========================
    json_file = "websites.json"
    existing_data = []

    if os.path.exists(json_file):
        with open(json_file, "r") as f:
            existing_data = json.load(f)

    # ========================= Merge New Entries =========================
    existing_links = {entry["link"] for entry in existing_data}
    new_entries = [entry for entry in captured_links if entry["link"] not in existing_links]

    # ========================= Save Updated Data =========================
    with open(json_file, "w") as f:
        json.dump(existing_data + new_entries, f, indent=4)

    # Return list of unique company names
    all_company_names = [entry["company_name"] for entry in (existing_data + new_entries)]
    return all_company_names

# ========================= Run the Function =========================
company_list = get_all_company_names()