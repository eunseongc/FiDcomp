#!/usr/bin/env python3
"""Given a data file with questions and retrieval results to use, run longchat to get responses.

Currently, this script only supports `longchat-13b-16k`.

The retrieval results are used in the exact order that they're given.
"""
import argparse
import dataclasses
import json
import logging
import math
import pathlib
import random
import sys
import re
from copy import deepcopy

import torch
from fastchat.model import get_conversation_template, load_model
from tqdm import tqdm
from xopen import xopen

from lost_in_the_middle.prompting import (
    Document,
    get_closedbook_qa_prompt,
    get_qa_prompt,
)

logger = logging.getLogger(__name__)
random.seed(0)


# Copied from https://github.com/DachengLi1/LongChat/blob/43d71f03d7711a2ab3b78ee8d1e38b65bb7fd22f/longeval/utils.py
def maybe_monkey_patch(model_name: str, longchat_flash_attn: bool, longchat_ratio: int):
    if "longchat" in model_name:
        from longchat.train.monkey_patch.llama_condense_monkey_patch import (
            replace_llama_with_condense,
        )

        replace_llama_with_condense(longchat_ratio)

        if longchat_flash_attn:
            from longchat.train.monkey_patch.llama_flash_attn_monkey_patch import (
                replace_llama_attn_with_flash_attn,
            )

            replace_llama_attn_with_flash_attn()
    import transformers  # noqa: F401


def main(
    input_path,
    is_compressed,
    model_name,
    temperature,
    top_p,
    batch_size,
    closedbook,
    prompt_mention_random_ordering,
    use_random_ordering,
    query_aware_contextualization,
    num_gpus,
    max_memory_per_gpu,
    longchat_flash_attn,
    longchat_ratio,
    max_new_tokens,
    output_path,
    qas_indices_set,
    use_compressed_text,
    ctxs_cutoff,
):
    if longchat_ratio != 8:
        raise ValueError("--longchat-ratio=8 is the only value currently supported.")

    # Create directory for output path if it doesn't exist.
    pathlib.Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    maybe_monkey_patch(model_name=model_name, longchat_flash_attn=longchat_flash_attn, longchat_ratio=longchat_ratio)

    examples = []
    prompts = []
    all_model_documents = []
    did_format_warn = False

    if qas_indices_set is not None:
        import pickle
        with open(qas_indices_set, "rb") as f:
            qas_indices_set = pickle.load(f)

    # Fetch all of the prompts
    with xopen(input_path) as fin:
        for i, line in enumerate(tqdm(fin)):
            if qas_indices_set is not None and i not in qas_indices_set:
                continue
            input_example = json.loads(line)
            if is_compressed:
                prompt = input_example["compressed_prompt"]
                documents = []
            else:
                # Get the prediction for the input example
                question = input_example["question"]
                if closedbook:
                    documents = []
                else:
                    documents = []
                    if ctxs_cutoff is not None:
                        input_example["ctxs"] = input_example["ctxs"][:ctxs_cutoff]
                    for ctx in deepcopy(input_example["ctxs"]):
                        documents.append(Document.from_dict(ctx))
                    if not documents:
                        raise ValueError(f"Did not find any documents for example: {input_example}")

                if use_random_ordering:
                    # Randomly order only the distractors (isgold is False), keeping isgold documents
                    # at their existing index.
                    (original_gold_index,) = [idx for idx, doc in enumerate(documents) if doc.isgold is True]
                    original_gold_document = documents[original_gold_index]
                    distractors = [doc for doc in documents if doc.isgold is False]
                    random.shuffle(distractors)
                    distractors.insert(original_gold_index, original_gold_document)
                    documents = distractors

                if closedbook:
                    prompt = get_closedbook_qa_prompt(question)
                else:
                    prompt = get_qa_prompt(
                        question,
                        documents,
                        mention_random_ordering=prompt_mention_random_ordering,
                        query_aware_contextualization=query_aware_contextualization,
                        use_compressed_text=use_compressed_text,
                    )

            if "chat" in model_name:
                if did_format_warn is False:
                    logger.warning(f"Model {model_name} appears to be an chat model, applying chat formatting")
                    did_format_warn = True
                prompt = format_chat_prompt(prompt)
                
            # prompt = re.sub(r' {2,}', ' ', prompt)
            # prompt_temp = prompt.split('\n\n')
            # documents_in_prompt = '\n'.join(prompt_temp[1:-1])
            # prompt = prompt_temp[0] + '\n\n' + documents_in_prompt + '\n\n' + prompt_temp[-1]
            # a = "Write a high-quality answer for the given question using only the provided search results (some of which might be irrelevant)."
            # b = "You are given a question and you MUST respond by EXTRACTING the answer (max 15 tokens) from one of the provided documents."
            # prompt = prompt.replace(a, b)
            prompts.append(prompt)
            examples.append(deepcopy(input_example))
            all_model_documents.append(documents)

    # Get responses for all of the prompts
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        raise ValueError("Unable to find CUDA device with torch. Please use a CUDA device to run this script.")

    model, tokenizer = load_model(
        model_name,
        device="cuda",
        num_gpus=num_gpus,
        max_gpu_memory=f"{max_memory_per_gpu}GiB",
        load_8bit=False,
        cpu_offloading=False,
        debug=False,
    )
    tokenizer.padding_side = "left"
    print(model)

    do_sample = temperature > 0.0

    responses = []
    for batched_prompts in tqdm(chunks(prompts, batch_size), total=math.ceil(len(prompts) / batch_size)):
        inputs = tokenizer(batched_prompts, return_tensors="pt", padding=True).to(model.device)
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=do_sample,
            temperature=temperature if do_sample else None,
            top_p=top_p if do_sample else None,
            # Disable use_cache if using longchat models with flash attention
            use_cache=not ("longchat" in model_name and longchat_flash_attn),
            return_dict_in_generate=False,
        )
        for i, generated_sequence in enumerate(outputs):
            input_ids = inputs["input_ids"][i]
            text = tokenizer.decode(generated_sequence, skip_special_tokens=True, clean_up_tokenization_spaces=True)

            if input_ids is None:
                prompt_length = 0
            else:
                prompt_length = len(
                    tokenizer.decode(
                        input_ids,
                        skip_special_tokens=True,
                        clean_up_tokenization_spaces=True,
                    )
                )
            new_text = text[prompt_length:]
            responses.append(new_text)

    with xopen(output_path, "w") as f:
        for example, model_documents, prompt, response in zip(examples, all_model_documents, prompts, responses):
            output_example = deepcopy(example)
            # Add some extra metadata to the output example
            output_example["model_prompt"] = prompt
            output_example["model_documents"] = [dataclasses.asdict(document) for document in model_documents]
            output_example["model_answer"] = response
            output_example["model"] = model_name
            output_example["model_temperature"] = temperature
            output_example["model_top_p"] = top_p
            output_example["model_prompt_mention_random_ordering"] = prompt_mention_random_ordering
            output_example["model_use_random_ordering"] = use_random_ordering
            f.write(json.dumps(output_example) + "\n")


def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def format_chat_prompt(input):
    conv = get_conversation_template("vicuna")
    conv.append_message(conv.roles[0], input)
    conv.append_message(conv.roles[1], None)
    prompt = conv.get_prompt()
    return prompt


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s - %(module)s - %(levelname)s - %(message)s", level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-path", help="Path to data with questions and documents to use.", required=True)
    parser.add_argument("--is-compressed", help="Whether the input is compressed", type=bool, default=False)
    parser.add_argument("--use-compressed-text", help="Whether the input is compressed", type=bool, default=False)
    parser.add_argument(
        "--model", help="Model to use in generating responses", required=True, choices=["lmsys/longchat-13b-16k"]
    )
    parser.add_argument("--temperature", help="Temperature to use in generation", type=float, default=0.0)
    parser.add_argument("--top-p", help="Top-p to use in generation", type=float, default=1.0)
    parser.add_argument("--batch-size", help="Batch size use in generation", type=int, default=8)
    parser.add_argument("--output-path", help="Path to write output file of generated responses", required=True)
    parser.add_argument("--num-gpus", help="Number of GPUs to use", type=int)
    parser.add_argument(
        "--closedbook", action="store_true", help="Run the model in closed-book mode (i.e., don't use documents)."
    )
    parser.add_argument(
        "--prompt-mention-random-ordering",
        action="store_true",
        help="Mention that search results are ordered randomly in the prompt",
    )
    parser.add_argument(
        "--use-random-ordering",
        action="store_true",
        help="Randomize the ordering of the distractors, rather than sorting by relevance.",
    )
    parser.add_argument(
        "--query-aware-contextualization",
        action="store_true",
        help="Place the question both before and after the documents.",
    )
    parser.add_argument(
        "--longchat-flash-attn",
        action="store_true",
        help="Only apply to longchat models. Whether to enable flash attention to save memory, but slower.",
    )
    parser.add_argument(
        "--longchat-ratio",
        type=int,
        default=8,
        help="Only apply to longchat models. Use ratio=8 for 16K context length model. Only ratio=8 is supported now.",
    )
    parser.add_argument(
        "--max-memory-per-gpu",
        help="Maximum memory to use per GPU (in GiB) for multi-device parallelism, e.g., 80",
        type=int,
    )
    parser.add_argument(
        "--max-new-tokens",
        help="Maximum number of new tokens to generate",
        type=int,
        default=100,
    )
    parser.add_argument("--qas-indices-set", help="Path to data with question indices", required=False, default=None)
    parser.add_argument("--ctxs-cutoff", help="ctxs_cutoff", type=int, default=None)
    args = parser.parse_args()

    logger.info("running %s", " ".join(sys.argv))
    main(
        args.input_path,
        args.is_compressed,
        args.model,
        args.temperature,
        args.top_p,
        args.batch_size,
        args.closedbook,
        args.prompt_mention_random_ordering,
        args.use_random_ordering,
        args.query_aware_contextualization,
        args.num_gpus,
        args.max_memory_per_gpu,
        args.longchat_flash_attn,
        args.longchat_ratio,
        args.max_new_tokens,
        args.output_path,
        args.qas_indices_set,
        args.use_compressed_text,
        args.ctxs_cutoff,
    )
    logger.info("finished running %s", sys.argv[0])
