from crewai import Task

from agents import content_agent, email_agent, research_agent, seo_agent

research_task = Task(
    description=(
        "Research the '{niche}' niche and shortlist up to 3 dropshipping "
        "product candidates. For each: estimated cost, suggested retail "
        "price, margin, shipping time, and a trend score out of 10. Rank "
        "them and recommend one to launch first."
    ),
    expected_output=(
        "A ranked list of up to 3 products, each with cost, price, margin, "
        "shipping time and trend score, plus a one-paragraph recommendation."
    ),
    agent=research_agent,
)

content_task = Task(
    description=(
        "Using the top recommended product from the research report, "
        "write: (1) one SEO-and-conversion product description, (2) 8 "
        "social captions with distinct hooks, (3) 2 short-form video "
        "scripts (Hook-Problem-Demo-CTA), and (4) 4 image-generation "
        "prompts."
    ),
    expected_output="A structured content package with the four sections above.",
    agent=content_agent,
    context=[research_task],
)

email_task = Task(
    description=(
        "Draft the base lifecycle email flows for the recommended product: "
        "a 3-email welcome series, a 3-email abandoned-cart series with an "
        "increasing discount, and a post-purchase review request."
    ),
    expected_output="Subject lines and body copy for all seven emails.",
    agent=email_agent,
    context=[research_task],
)

seo_task = Task(
    description=(
        "Summarize the research and content outputs into a single approval "
        "digest for the business owner: what is being proposed, the "
        "estimated cost, the expected ROAS/margin, and one clear yes/no "
        "question. Send it using the approval tool."
    ),
    expected_output="Confirmation that the approval digest was sent, plus its exact text.",
    agent=seo_agent,
    context=[research_task, content_task],
)
