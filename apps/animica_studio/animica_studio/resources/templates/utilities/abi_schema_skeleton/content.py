ABI_SCHEMA = {
    "contract": "{{CONTRACT_NAME}}",
    "version": "1.0.0",
    "functions": [
        {
            "name": "set_value",
            "inputs": [{"name": "value", "type": "u256"}],
            "outputs": []
        },
        {
            "name": "get_value",
            "inputs": [],
            "outputs": [{"name": "value", "type": "u256"}]
        }
    ],
    "events": []
}
