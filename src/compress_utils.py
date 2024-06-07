import re
import tiktoken
import numpy as np

from collections import Counter
from nltk import sent_tokenize
from IPython import embed

INSTRUCTION = "Write a high-quality answer for the given question using only the provided search results (some of which might be irrelevant)."
QUESTION_TEMPLATE = "Question: {}\nAnswer:"
GPT_TOKENIZER = tiktoken.encoding_for_model("gpt-3.5-turbo")

_dict = {
    "narrativeqa": "{compressed_prompt}\n\n{question}",
    "qasper": "{compressed_prompt}\n\n {question}",
    "multifieldqa_en": "\n\n{compressed_prompt}\n\n{question}",
    "hotpotqa": "{compressed_prompt}\n\n{question}",
    "2wikimqa": "{compressed_prompt}\n\n{question}",
    "musique": "{compressed_prompt}\n\n{question}",
    "gov_report": "{compressed_prompt}\n\n{question}",
    "qmsum": "{compressed_prompt}\n\n{question}",
    "multi_news": "{compressed_prompt}\n\n{question}",
    "trec": "{compressed_prompt}\n{question}",
    "triviaqa": "{compressed_prompt}\n\n{question}",
    "samsum": "{compressed_prompt}\n\n{question}",
    "passage_count": "{compressed_prompt}\n\n{question}",
    "passage_retrieval_en": "{compressed_prompt}\n\n{question}",
    "lcc": "{compressed_prompt}{question}",
    "repobench-p": "{compressed_prompt}{question}"
}


_dict_wo_instruction = {
    "narrativeqa": "{compressed_prompt}\n\nNow, answer the question based on the story asconcisely as you can, using a single phrase if possible. Do not provide any explanation.\n\nQuestion: {question}",
    "qasper": "{compressed_prompt}\n\n Answer the question based on the above article as concisely as you can, using a single phrase or sentence if possible. If the question cannot be answered based on the information in the article, write \"unanswerable\". If the question is a yes/no question, answer \"yes\", \"no\", or \"unanswerable\". Do not provide any explanation.\n\nQuestion: {question}",
    "multifieldqa_en": "{compressed_prompt}\n\nNow, answer the following question based on the above text, only give me the answer and do not output any other words.\n\nQuestion: {question}",
    "hotpotqa": "{compressed_prompt}\n\nAnswer the question based on the given passages. Only give me the answer and do not output any other words.\n\nQuestion: {question}",
    "2wikimqa": "{compressed_prompt}\n\nAnswer the question based on the given passages. Only give me the answer and do not output any other words.\n\nQuestion: {question}",
    "musique": "{compressed_prompt}\n\nAnswer the question based on the given passages. Only give me the answer and do not output any other words.\n\nQuestion: {question}",
    "gov_report": "{compressed_prompt}",
    "qmsum": "{compressed_prompt}\n\nNow, answer the query based on the above meeting transcript in one or more sentences.\n\nQuery: {question}",
    "multi_news": "{compressed_prompt}",
    "trec": "{compressed_prompt}\n{question}",
    "triviaqa": "{compressed_prompt}\n\n{question}",
    "samsum": "{compressed_prompt}\n\n{question}",
    "passage_count": "{compressed_prompt}",
    "passage_retrieval_en": "{compressed_prompt}\n\nThe following is an abstract.\n\n{question}",
    "lcc": "{compressed_prompt}",
    "repobench-p": "{compressed_prompt}{question}"
}

def get_prompt(ctxs, qas, dataset, use_org_idx=True):
    if 'longbench' in dataset:
        dataname = '_'.join(dataset.split('_')[1:])
        
        prompt = ""
        cur_ctx_id = 0
        for ctx in ctxs:
            if ctx['id'].split('-')[0] == cur_ctx_id:
                if prompt == "":
                    prompt = ctx['text']
                else:
                    prompt = prompt + " " + ctx['text']
            else:
                prompt = prompt + '\n' + ctx['text']
                cur_ctx_id = ctx['id'].split('-')[0]
        # prompt = _dict[dataname].format(compressed_prompt=prompt, question=qas['question'])
        prompt = _dict_wo_instruction[dataname].format(compressed_prompt=prompt, question=qas['question'])
        # prompt = prompt.strip('\n') + "\n\n" + qas['question']
        # prompt = "\n".join([ctx['text'] for ctx in ctxs] + [qas['question']])
    else:
        ctxs_formatted = []
        for c_i, ctx in enumerate(ctxs):
            idx = ctx['org_idx'] if use_org_idx else c_i + 1
            ctxs_formatted.append(f"Document [{idx}](Title: {ctx['title']}) {ctx['text']}")
        prompt = INSTRUCTION + '\n\n' + '\n'.join(ctxs_formatted) + '\n\n' + QUESTION_TEMPLATE.format(qas['question'])

    return prompt



def gini(x):
    mean_absolute_diff = np.abs(np.subtract.outer(x, x)).mean()
    relative_mean_absolute_diff = mean_absolute_diff/np.mean(x)
    g = 0.5 * relative_mean_absolute_diff
    return g


def get_ctx_start_indices(tokenizer, question, titles, pattern_str):
    
    if titles[0] is not None:
        assert [title for title in titles if title is not None] == titles
    else:
        for title in titles:
            assert title is None

    pattern_token_ids = tokenizer.encode(pattern_str, add_special_tokens=False)
    len_pattern = len(pattern_token_ids)    

    formats = ["question: {} title: {}", "question: {}"]
    if titles[0] is not None:
        question_title = []
        for title in titles:
            question_title.append(formats[0].format(question, title))
        question_title_input_ids = tokenizer.batch_encode_plus(question_title, add_special_tokens=False)['input_ids']
        ctx_start_indices_list = []
        for question_title_input_id in question_title_input_ids:
            ctx_start_indices_list.append(len(question_title_input_id) + len_pattern)
    else:
        question = formats[1].format(question)
        question_input_ids = tokenizer.encode(question, add_special_tokens=False)[-252:]
            
        ctx_start_indices_list = [len(question_input_ids) + len_pattern for _ in range(len(titles))]

    return ctx_start_indices_list


def get_ctx_scores(batch_scores, mode: str, question_mode: str, include_end_token: bool, tokenizer, question, titles, pattern_str):
    ## Only question scoring 추가
    if question_mode == 'include':
        batch_scores_ctxs = batch_scores
    elif question_mode == 'exclude':
        ctx_start_indices = get_ctx_start_indices(tokenizer, question, titles, pattern_str)
        batch_scores_ctxs = [batch_score[ctx_start_idx:] for batch_score, ctx_start_idx in zip(batch_scores, ctx_start_indices)]
    else:
        raise ValueError(f"Invalid question mode: {question_mode}")
    # elif question_mode == 'exclude':
    #     print("Question mode is exclude, WE SHOULD NOT USE THIS\n" * 8)
    #     ctx_start_idx = get_ctx_start_indices(batch_token_ids, tokenizer, pattern_str)
    #     batch_scores_ctxs = batch_scores[:, ctx_start_idx:]
    # elif question_mode =='only':
    #     print("Question mode is ONLY, WE SHOULD NOT USE THIS\n" * 8)
    #     ctx_start_idx = get_ctx_start_indices(batch_token_ids, tokenizer, pattern_str)
    #     pattern_token_ids = tokenizer.encode(pattern_str, add_special_tokens=False)
    #     len_pattern = len(pattern_token_ids) 
    #     batch_scores_ctxs = batch_scores[:, :ctx_start_idx - len_pattern]
        
    if include_end_token:
        end = None
    else:
        end = -1

    ctx_scores = []
    for scores in batch_scores_ctxs:
        if mode == 'mean':
            ctx_scores.append(scores[scores != 0][:end].mean())
        elif mode == 'max':
            ctx_scores.append(scores[scores != 0][:end].max())
        elif mode == 'sum': ## Not fair as it is based on the length of the context
            ctx_scores.append(scores[scores != 0][:end].sum())
        else:
            raise ValueError(f"Invalid mode: {mode}")

    return ctx_scores


def compress_contexts(args, batch_scores, batch_token_ids, tokenizer, ctxs, ctx_comp_len: int, ctx_score_cumsum, question, pattern_str):
    ############################################
    ### Context compression using FiD score ###
    ############################################

    do_sort_ctx=args.do_sort_ctx
    ctx_score_mode=args.ctx_score_mode
    question_mode=args.question_mode
    include_end_token=args.include_end_token
    titles = [ctx['title'] for ctx in ctxs]
    ctx_scores = get_ctx_scores(batch_scores, ctx_score_mode, question_mode, include_end_token, tokenizer, question, titles, pattern_str)
    ctx_indices_sorted = np.argsort(ctx_scores)[::-1].tolist()
    ctx_indices = []
    len_total = len(GPT_TOKENIZER.encode(get_prompt([], {'question': question}, args.dataset, use_org_idx=True)))

    if ctx_score_cumsum is not None:
        ## By score percentile
        ctx_scores = ctx_scores/np.sum(ctx_scores)
        ### THIS IS WHAT DIFFERENCE FROM THE 0530 VERSION
        # cum_ctx_index = np.where(ctx_scores[ctx_scores.argsort()[::-1]].cumsum() >= ctx_score_cumsum)[0][0] + 1
        #####
        cum_ctx_index = np.where(ctx_scores[ctx_scores.argsort()[::-1]].cumsum() > ctx_score_cumsum)[0][0]
        if cum_ctx_index == 0:
            cum_ctx_index = 1
        #####
        ctx_indices = np.argsort(ctx_scores)[::-1][:cum_ctx_index].tolist()
    else:
        for idx in ctx_indices_sorted:
            if 'longbench' in args.dataset:
                # Chunk only option
                if not args.comp_sent and abs(len_total - ctx_comp_len) < abs(len_total + len(GPT_TOKENIZER.encode(f"{ctxs[idx]['text']}")) - ctx_comp_len):
                    break
                len_total += len(GPT_TOKENIZER.encode(ctxs[idx]['text']))
            else:
                # Chunk only option
                if not args.comp_sent and abs(len_total - ctx_comp_len) < abs(len_total + len(GPT_TOKENIZER.encode(f"Document [{idx}](Title: {ctxs[idx]['title']}) {ctxs[idx]['text']}")) - ctx_comp_len):
                    break
                len_total += len(GPT_TOKENIZER.encode(f"Document [{idx}](Title: {ctxs[idx]['title']}) {ctxs[idx]['text']}"))

            ctx_indices.append(idx)
            if len_total >= ctx_comp_len:
                break
        

    for idx in ctx_indices:
        ctxs[idx]['ctx_score'] = ctx_scores[idx]

    if do_sort_ctx:
        ## Remain sorted order
        ctx_indices_selected = ctx_indices
    else:
        ## Restore the original order
        ctx_indices_selected = sorted(ctx_indices)

    ctxs = [ctxs[i] for i in ctx_indices_selected]
    batch_scores_selected = batch_scores[ctx_indices_selected] ## result shape: (cut_off, max_len)
    batch_token_ids_selected = batch_token_ids[ctx_indices_selected] ## result shape: (cut_off, max_len)

    return batch_scores_selected, batch_token_ids_selected, ctxs, ctx_indices


def compress_sentences(batch_scores, batch_token_ids, tokenizer, ctxs, ctx_indices_sorted, sent_comp_len: int, adaptive_sent_comp: bool, question, pattern_str: str, pow, constraint_1_sent):
    ############################################
    ### Sentence compression using FiD score ###
    ############################################
    # Preparing lists to store the results
    
    ## Decode 해서 더해줄 것이 아니라, 점수로... 어떻게 해야할듯..?
    ## [ES] batch_len_context is not used
    titles = [ctx['title'] for ctx in ctxs]
    ctx_start_indices = get_ctx_start_indices(tokenizer, question, titles, pattern_str)
    batch_eos_token_idx, batch_len_context = [], []
    for ctx_i, token_ids in enumerate(batch_token_ids):
        eos_token_idx = np.where(token_ids == 1)[0][-1]
        batch_eos_token_idx.append(eos_token_idx)
        batch_len_context.append(eos_token_idx - ctx_start_indices[ctx_i])

    batch_scores_context = [batch_scores[i][ctx_start_indices[i]:eos_token_idx] for i, eos_token_idx in enumerate(batch_eos_token_idx)]
    batch_token_ids_context = [batch_token_ids[i][ctx_start_indices[i]:eos_token_idx] for i, eos_token_idx in enumerate(batch_eos_token_idx)]

    ## split before sent_tokenize
    # sents_list = []
    # for ctx in ctxs:
    #     lines = ctx['text'].split('\n')
    #     sents = []
    #     for line in lines:
    #         sents.extend(sent_tokenize(line))
    #     sents_list.append(sents)

    sents_list = [sent_tokenize(ctx['text']) for ctx in ctxs]
    split_token_ctxs = []
    for ctx_i, ctx in enumerate(ctxs):
        split_token_ctx = ['']
        # find sent_tokenize split tokens
        ctx_text = ctx['text']
        for sent_index in range(len(sents_list[ctx_i])-1):
            prev_sent = sents_list[ctx_i][sent_index]
            len_prev_sent = len(prev_sent)
            cur_sent = sents_list[ctx_i][sent_index+1]

            prev_sent_end_idx = len_prev_sent
            cur_sent_start_idx = len_prev_sent + ctx_text[len_prev_sent:].find(cur_sent)

            split_token = ctx_text[prev_sent_end_idx:cur_sent_start_idx]
            # split_token = ctx['text'][ctx['text'].find(cur_sent) - 1]
            split_token_ctx.append(split_token)
            ctx_text = ctx_text[cur_sent_start_idx:]

        split_token_ctxs.append(split_token_ctx)
        
    sent_mean_score_ctxs = []
    sent_scores_ctxs = []
    sent_token_ids_ctxs = []
    for context_score, context_ids, sents in zip(batch_scores_context, batch_token_ids_context, sents_list):
        try:
            sent_len_list = [len(tokens_sent) for tokens_sent in tokenizer.batch_encode_plus(sents, add_special_tokens=False)['input_ids']]
        except:
            embed()

        cum_len_list = [sum(sent_len_list[:i+1]) for i in range(len(sent_len_list))]
        start_idx = 0
        sent_mean_score_ctx = []
        sent_scores_ctx = []
        sent_token_idx_ctx = []
        for cum_len in cum_len_list:
            if start_idx >= context_score.shape[0]:
                continue
            sent_mean_score_ctx.append(np.mean(context_score[start_idx:cum_len]))
            sent_scores_ctx.append(context_score[start_idx:cum_len])
            sent_token_idx_ctx.append(context_ids[start_idx:cum_len])
            ## Update     
            start_idx = cum_len

        sent_mean_score_ctxs.append(np.array(sent_mean_score_ctx))
        sent_scores_ctxs.append(sent_scores_ctx)
        sent_token_ids_ctxs.append(sent_token_idx_ctx)

    num_ctxs = len(ctxs)

    if adaptive_sent_comp and num_ctxs > 1:
        ctx_scores = [ctx['ctx_score'] for ctx in ctxs]
        ## Adaptive sentence compression
        ## sum should be the sent_comp_len
        # d = 2 * sent_comp_len / (num_ctxs * (num_ctxs - 1))
        # comp_len_per_ctx = []
        # for i in range(num_ctxs):
        #     comp_len_per_ctx.append(int(d * i))
        ctx_scores = np.power(ctx_scores, pow)
        comp_len_per_ctx = (((1/ctx_scores)/np.sum(1/ctx_scores)) * sent_comp_len).astype(int).tolist()
        comp_len_per_ctx = sorted(comp_len_per_ctx)
    else:
        comp_len_per_ctx = [int(sent_comp_len / num_ctxs) for _ in range(num_ctxs)]
        
    new_token_ids_ctxs = {}
    new_scores_ctxs = {}
    rank_map = {ctx_i: rank for rank, ctx_i in enumerate(ctx_indices_sorted)}
    ctx_idx2ctx_i = {ctx['org_idx'] - 1: i for i, ctx in enumerate(ctxs)}

    # for i, idx in enumerate(cur_ctx_indices):
    reversed_ctx_indices_sorted = list(reversed(ctx_indices_sorted))

    ## idx -> ctx_idx / i --> cur_i
    # for i, idx in reversed(list(enumerate(cur_ctx_indices))):

    total_comp_len = 0
    ctx_removal_indices = []
    for idx in reversed_ctx_indices_sorted:
        rank = rank_map[idx]
        i = ctx_idx2ctx_i[idx]
        if ctxs[i]['title'] is not None:
            ctx_len = len(GPT_TOKENIZER.encode(f"Document [{ctxs[i]['org_idx']}](Title: {ctxs[i]['title']}) {ctxs[i]['text']}"))
        else:
            ctx_len = len(GPT_TOKENIZER.encode(ctxs[i]['text']))

        sent_mean_score_ctx = sent_mean_score_ctxs[i]
        sents = sents_list[i]
        
        if len(sent_mean_score_ctx) == 0:
            print("No sentence in the context, tokenizer can not handle this case")
            ctx_removal_indices.append(i)
            continue
            # embed()
        rest_sents = None
        if len(sent_mean_score_ctx) != len(sents):
            ## When the length of the question + context was longer than the max length of the FiD, this can happen
            ## In this case, we should match sents to sent_mean_score_ctx
            # print(f"Number of sent mean scores in chunk is different to the number of sents in the context. {len(sent_mean_score_ctx)} vs {len(sents)}")
            rest_sents = sents[len(sent_mean_score_ctx):]
            sents = sents[:len(sent_mean_score_ctx)]
            
        comp_len = comp_len_per_ctx[rank]
        argsort_sent = np.argsort(sent_mean_score_ctx) ## Descending order
        cur_comp_len = 0
        # print(total_comp_len, sent_comp_len, comp_len, len(sents), comp_len_per_ctx, end=' / ')
        embed()
        if total_comp_len >= sent_comp_len or comp_len == 0:
            # print(f'comp_len is 0, skip {rank}th context. out of {num_ctxs} contexts. / total_comp_len: {total_comp_len} / sent_comp_len: {sent_comp_len}')
            new_scores_ctxs[idx] = batch_scores_context[i]
            new_token_ids_ctxs[idx] = batch_token_ids_context[i]
        else:
            ### Sentence compression based on the FiD score
            for r, sent_index in enumerate(argsort_sent):
                cur_sent_len = len(GPT_TOKENIZER.encode(split_token_ctxs[i][sent_index] + sents[sent_index]))
                cur_comp_len += cur_sent_len
                total_comp_len += cur_sent_len
                if total_comp_len >= sent_comp_len:
                    ## Check if the last sentence is needed
                    # compare with previous total comp len, and if the difference is smaller, restore the last sentence
                    if np.abs(total_comp_len - cur_sent_len - sent_comp_len) < np.abs(total_comp_len - sent_comp_len):
                        r = r - 1
                        total_comp_len -= cur_sent_len
                        cur_comp_len -= cur_sent_len
                    break
                elif cur_comp_len >= comp_len:
                    break
            r = r + 1 ## sents from r+1 will be appended
            ######################################################
            
            if constraint_1_sent == True and r == len(argsort_sent):
                r = r - 1

            if r == len(argsort_sent):
                ctx_removal_indices.append(i)
                actural_comp_len = ctx_len
            else:
                argsort_sent = argsort_sent[r:]
                sent_indices_selected = np.sort(argsort_sent)
                sent_comp_text = ""
                for sent_index in sent_indices_selected:
                    sent_comp_text += split_token_ctxs[i][sent_index] + sents[sent_index]
                
                if rest_sents is not None:
                    for sent_index, rest_sent in enumerate(rest_sents):
                        sent_comp_text += split_token_ctxs[i][sent_index + len(sents)] + rest_sent
                    
                ctxs[i]['text'] = sent_comp_text
                if ctxs[i]['title'] is not None:
                    after_sent_comp_len = len(GPT_TOKENIZER.encode(f"Document [{ctxs[i]['org_idx']}](Title: {ctxs[i]['title']}) {sent_comp_text}"))
                else:
                    after_sent_comp_len = len(GPT_TOKENIZER.encode(sent_comp_text))

                actural_comp_len = ctx_len - after_sent_comp_len

                new_scores_ctx = np.concatenate([sent_scores_ctxs[i][sent_index] for sent_index in sent_indices_selected])
                new_scores_ctxs[idx] = new_scores_ctx
                new_token_ids_ctx = np.concatenate([sent_token_ids_ctxs[i][sent_index] for sent_index in sent_indices_selected])
                new_token_ids_ctxs[idx] = new_token_ids_ctx

            diff = cur_comp_len - actural_comp_len
            total_comp_len -= diff
            cur_comp_len = actural_comp_len
            ### Dealing exceed cur_comp_len --> reduce comp_len from the top ranked context
            if cur_comp_len > comp_len:
                exceed_len = cur_comp_len - comp_len
                ## iterate from the top ranked context
                for r_i, comp_len_ctx in enumerate(comp_len_per_ctx):
                    if exceed_len == 0 or r_i == rank:
                        break
                    if comp_len_ctx > 0:
                        update_comp_len_ctx = max(0, comp_len_ctx - exceed_len)
                        exceed_len = max(0, exceed_len - comp_len_ctx)
                        comp_len_per_ctx[r_i] = update_comp_len_ctx
            else:
                if rank == 0:
                    pass
                else:
                    comp_len_per_ctx[rank-1] += comp_len - cur_comp_len

    if len(ctx_removal_indices) > 0:
        ctxs = [ctx for i, ctx in enumerate(ctxs) if i not in set(ctx_removal_indices)]

    ## Reorder new_scores_ctxs and new_token_ids_ctxs
    new_scores_ctxs = [new_scores_ctxs[ctx['org_idx']-1] for ctx in ctxs]
    new_token_ids_ctxs = [new_token_ids_ctxs[ctx['org_idx']-1] for ctx in ctxs]
    
    

    # grad_comp_ratio_sent = np.linspace(sent_low, sent_high, len(ctxs ))[::-1]
    ## Using percentile
    # for ctx_i, (sent_mean_score_ctx, comp_ratio) in enumerate(zip(sent_mean_score_ctxs, grad_comp_ratio_sent)):
    #     ## Select sent based on sent_mean_score_ctx up to comp_ratio (stop if over comp_ratio)
    #     sent_indices_selected = []
    #     cum_score = 0
    #     sent_mean_score_ctx_norm_sorted = np.sort(sent_mean_score_ctx/sent_mean_score_ctx.sum())[::-1]
    #     indices_sorted = np.argsort(sent_mean_score_ctx)[::-1]
    #     for idx, sent_score in enumerate(sent_mean_score_ctx_norm_sorted):
    #         cum_score += sent_score
    #         sent_indices_selected.append(indices_sorted[idx])
    #         if cum_score > comp_ratio:
    #             break
    #     sent_indices_selected = np.sort(sent_indices_selected)
    #     sent_comp_text = ""
    #     for i in sent_indices_selected:
    #         sent_comp_text += split_token_ctxs[ctx_i][i] + sents_list[ctx_i][i]
    #     # sent_comp_text = ' '.join([sents_list[ctx_i][i] for i in sent_indices_selected])
    #     ctxs[ctx_i]['text'] = sent_comp_text

    #     new_scores_ctx = np.concatenate([sent_token_ids_ctxs[ctx_i][i] for i in sent_indices_selected])
    #     new_scores_ctxs.append(new_scores_ctx)
    #     new_token_ids_ctx = np.concatenate([sent_token_ids_ctxs[ctx_i][i] for i in sent_indices_selected])
    #     new_token_ids_ctxs.append(new_token_ids_ctx)
    return new_scores_ctxs, new_token_ids_ctxs, ctxs


def compress_tokens(batch_scores, batch_token_ids, ctxs, token_lamb, tokenizer):
    ############################################
    ### 3. Token compression using FiD score ###
    ############################################
    len_ctxs = [ctx_scores.shape[0] for ctx_scores in batch_scores]

    ## Concatenate all the scores and ids in the batch
    batch_scores = np.concatenate(batch_scores)
    batch_token_ids = np.concatenate(batch_token_ids)

    # target_len = max(int(batch_scores.shape[0] * token_lamb), 100) ## Not cur_len, as it is the length based on chatgpt_tok
    target_len = int(batch_scores.shape[0] * token_lamb)

    cum_len_list = [sum(len_ctxs[:i+1]) for i in range(len(len_ctxs))]
    sorted_target_indices = np.sort(batch_scores.argsort()[::-1][:target_len])

    start = 0
    for cum_len, ctx in zip(cum_len_list, ctxs):
        end = cum_len
        cur_target_indices = sorted_target_indices[(sorted_target_indices >= start) & (sorted_target_indices < end)]
        empty_token_indices = np.where(batch_token_ids[start:end] == 3)[0] + start
        cur_target_indices = np.sort(np.union1d(cur_target_indices, empty_token_indices))
        new_batch_token_ids = batch_token_ids[cur_target_indices]
        new_batch_token_ids_filtered = new_batch_token_ids[new_batch_token_ids != 2]
        tok_comp_text = tokenizer.decode(new_batch_token_ids_filtered)
        tok_comp_text = re.sub(r' {2,}', ' ', tok_comp_text)
        ctx['text'] = tok_comp_text
        start = end

    ## [ES] Should I return batch_token_scores, batch_token_ids too?
    return ctxs



'''

span_em_list = []
for pred in preds:
    cnt = pred['model_prompt'].count('Document [')
    gold_answers = pred["answers"]
    model_answer = pred["model_answer"]
    span_em_list.append(best_subspan_em(prediction=model_answer, ground_truths=gold_answers))
print(f'{100 * np.mean(span_em_list):.2f}')




3.86
66.71
4.46
67.07
6.96
66.81
7.29
66.55

3.88
51.76
4.47
51.50
6.96
51.15
7.27
50.32

0.05 (문서 압축을 많이한 것)
total: 58.89 low_indices: 66.71 / high_indices: 51.76

0.1
total: 58.92 low_indices: 67.07 / high_indices: 51.50

0.15
total: 58.65 low_indices: 66.64 / high_indices: 51.36

0.2
total: 58.84 low_indices: 66.98 / high_indices: 51.43

0.25

0.3
total: 58.62 low_indices: 66.81 / high_indices: 51.15

0.35 (문서 압축을 조금한 것)
total: 58.06 low_indices: 66.55 / high_indices: 50.32



3.87
58.89
51.76
66.71
4.47
58.92
51.50
67.07
5.06
58.65
51.36
66.64
5.90
58.84
51.43
66.98





for rate in [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4]
for rate in [0.05, 0.3]:
for rate in [0.05, 0.1, 0.15, 0.2]:
    with xopen(f'fid_500_ctxTrue_sentTrue{rate}_tokFalse1.0_orgidxTrue_Llama-2-13b-chat-hf.jsonl.gz') as f:
        preds = [json.loads(l) for l in f]
        doc_num_list = []
        span_em_list = []
        span_em_list_low = []
        span_em_list_high = []

        for p_i, pred in enumerate(preds):
            cnt = pred['model_prompt'].count('Document [')
            doc_num_list.append(cnt)
            gold_answers = pred["answers"]
            model_answer = pred["model_answer"]
            span_em = best_subspan_em(prediction=model_answer, ground_truths=gold_answers)
            span_em_list.append(span_em)
            if p_i in low_indices:
                span_em_list_low.append(span_em)
            else:
                span_em_list_high.append(span_em)
        print(f'{np.mean(doc_num_list):.2f}')
        print(f'{100 * np.mean(span_em_list):.2f}')
        print(f'{100 * np.mean(span_em_list_low):.2f}')
        print(f'{100 * np.mean(span_em_list_high):.2f}')
        
doc_num_list = []
for d in data:
    cnt = d['compressed_prompt'].count('Document [')
    doc_num_list.append(cnt)
print(f'{np.mean(doc_num_list):.2f}')

'''