from podlets.upscale_alias import parse_upscale


def test_upscale_alias_keeps_job_options_and_forwards_model_options():
    ns = parse_upscale(
        [
            "/data/input",
            "experiment/",
            "--output-dir",
            "/data/results",
            "--mem",
            "8G",
            "--model",
            "realesrgan-x2plus",
            "--all-images",
            "--only-bucket",
            "medium",
            "--pre-medium",
            "1.25",
            "--scales",
            "2",
        ]
    )

    assert ns.command == "upscale"
    assert ns.operands == ["/data/input", "experiment/"]
    assert ns.output_dir == "/data/results"
    assert ns.mem == "8G"
    assert ns.extra == [
        "--model",
        "realesrgan-x2plus",
        "--all-images",
        "--only-bucket",
        "medium",
        "--pre-medium",
        "1.25",
        "--scales",
        "2",
    ]
