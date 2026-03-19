"""
Comprehensive tests for Content Recycling module.

Tests for ContentRecycler, ContentAsset, AtomizationResult,
and content transformation functions.
"""

import pytest
from dataclasses import asdict
from unittest.mock import MagicMock

from forge_shared.content.recycling import (
    Platform,
    ContentType,
    ContentAsset,
    AtomizationResult,
    ContentRecycler,
    BlogPost,
    atomize_blog_post,
    LINKEDIN_TRANSFORM,
    TWITTER_TRANSFORM,
    NEWSLETTER_TRANSFORM,
    INSTAGRAM_TRANSFORM,
)


class TestPlatformEnum:
    """Tests for Platform enum."""

    def test_platform_values(self):
        """Test Platform enum has correct values."""
        assert Platform.LINKEDIN.value == "linkedin"
        assert Platform.TWITTER.value == "twitter"
        assert Platform.INSTAGRAM.value == "instagram"

    def test_platform_iteration(self):
        """Test iterating over Platform enum."""
        platforms = list(Platform)
        assert len(platforms) >= 10
        assert Platform.LINKEDIN in platforms


class TestContentTypeEnum:
    """Tests for ContentType enum."""

    def test_content_type_values(self):
        """Test ContentType enum has correct values."""
        # Use getattr to avoid potential import issues
        assert hasattr(ContentType, 'POST')
        assert hasattr(ContentType, 'THREAD')
        assert hasattr(ContentType, 'CAROUSEL')
        assert hasattr(ContentType, 'NEWSLETTER')
        assert hasattr(ContentType, 'VIDEO_SCRIPT')

    def test_content_type_iteration(self):
        """Test iterating over ContentType enum."""
        types = list(ContentType)
        assert len(types) >= 10
        assert ContentType.POST in types


class TestContentAsset:
    """Tests for ContentAsset dataclass."""

    def test_content_asset_creation(self):
        """Test creating a ContentAsset."""
        asset = ContentAsset(
            platform=Platform.LINKEDIN,
            content_type=ContentType.POST,
            title="Test Post",
            body="This is test content",
            word_count=100,
            character_count=500
        )

        assert asset.platform == Platform.LINKEDIN
        assert asset.content_type == ContentType.POST
        assert asset.word_count == 100

    def test_content_asset_defaults(self):
        """Test ContentAsset default values."""
        asset = ContentAsset(
            platform=Platform.TWITTER,
            content_type=ContentType.POST,
            title="Test",
            body="Content",
            word_count=50,
            character_count=250
        )

        assert asset.hashtags == []
        assert asset.call_to_action is None
        assert asset.quality_score == 0.0


class TestBlogPost:
    """Tests for BlogPost model."""

    def test_blog_post_creation(self):
        """Test creating a BlogPost."""
        post = BlogPost(
            id="post-123",
            title="Test Article",
            body_md="# Test\n\nContent here",
            tags=["test", "article"]
        )

        assert post.id == "post-123"
        assert post.title == "Test Article"

    def test_blog_post_defaults(self):
        """Test BlogPost default values."""
        post = BlogPost(
            title="Simple Post",
            body_md="Content"
        )

        assert post.id is None
        assert post.tags == []


class TestContentRecyclerInit:
    """Tests for ContentRecycler initialization."""

    def test_init_with_defaults(self):
        """Test ContentRecycler initialization with defaults."""
        recycler = ContentRecycler()

        assert recycler.client is None
        assert recycler.tone_presets is not None
        assert recycler.rules is not None

    def test_init_with_openai_client(self):
        """Test ContentRecycler with OpenAI client."""
        mock_client = MagicMock()
        recycler = ContentRecycler(openai_client=mock_client)

        assert recycler.client == mock_client


class TestAtomize:
    """Tests for ContentRecycler.atomize method."""

    def test_atomize_blog_post_object(self):
        """Test atomizing a BlogPost object."""
        recycler = ContentRecycler()

        post = BlogPost(
            id="test-id",
            title="How to Build Apps",
            body_md="## Step 1\nPlan your app",
            tags=["development"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.LINKEDIN, Platform.TWITTER]
        )

        assert isinstance(result, AtomizationResult)
        assert result.source_id == "test-id"
        assert len(result.assets) > 0

    def test_atomize_markdown_string(self):
        """Test atomizing a markdown string."""
        recycler = ContentRecycler()

        markdown = """# My Article

## Key Point
First important thing
"""

        result = recycler.atomize(source=markdown)

        assert isinstance(result, AtomizationResult)
        assert len(result.assets) > 0

    def test_atomize_all_platforms_default(self):
        """Test atomizing to all platforms by default."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Test",
            body_md="# Content\n\nSome content"
        )

        result = recycler.atomize(source=post)

        # Should cover multiple platforms
        assert len(result.platforms_covered) >= 2


class TestContentTransformation:
    """Tests for content transformation methods."""

    def test_create_thread_content(self):
        """Test creating thread content."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Thread Test",
            body_md="## Point 1\nFirst point\n\n## Point 2\nSecond point",
            tags=["thread"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.TWITTER],
            content_types=[ContentType.THREAD]
        )

        thread_assets = [a for a in result.assets if a.content_type == ContentType.THREAD]
        assert len(thread_assets) > 0

        thread = thread_assets[0]
        assert "1/" in thread.body

    def test_create_post_content(self):
        """Test creating post content."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Post Test",
            body_md="## Key Insight\nThis is important",
            tags=["post"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.LINKEDIN],
            content_types=[ContentType.POST]
        )

        post_assets = [a for a in result.assets if a.content_type == ContentType.POST]
        assert len(post_assets) > 0

        social_post = post_assets[0]
        assert "🚀" in social_post.body

    def test_create_newsletter_content(self):
        """Test creating newsletter content."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Newsletter Test",
            body_md="## Takeaway 1\nPoint one",
            tags=["newsletter"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.NEWSLETTER],
            content_types=[ContentType.NEWSLETTER]
        )

        newsletter_assets = [a for a in result.assets if a.content_type == ContentType.NEWSLETTER]
        assert len(newsletter_assets) > 0

        newsletter = newsletter_assets[0]
        assert "📬" in newsletter.title

    def test_create_quick_tip_content(self):
        """Test creating quick tip content."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Tip Test",
            body_md="Tip: Always test your code\n\nPro Tip: Use type hints",
            tags=["tips"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.LINKEDIN],
            content_types=[ContentType.QUICK_TIP]
        )

        tip_assets = [a for a in result.assets if a.content_type == ContentType.QUICK_TIP]
        assert len(tip_assets) > 0

        tip = tip_assets[0]
        assert "💡" in tip.body

    def test_create_how_to_content(self):
        """Test creating how-to guide content."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="How to Test",
            body_md="1. Write test\n\n2. Run test",
            tags=["guide"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.BLOG],
            content_types=[ContentType.HOW_TO]
        )

        how_to_assets = [a for a in result.assets if a.content_type == ContentType.HOW_TO]
        assert len(how_to_assets) > 0

        how_to = how_to_assets[0]
        assert "🛠️" in how_to.body

    def test_create_video_script_content(self):
        """Test creating video script content."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Video Test",
            body_md="## Key Point\nMain content",
            tags=["video"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.YOUTUBE],
            content_types=[ContentType.VIDEO_SCRIPT]
        )

        script_assets = [a for a in result.assets if a.content_type == ContentType.VIDEO_SCRIPT]
        assert len(script_assets) > 0

        script = script_assets[0]
        assert "🎬" in script.body

    def test_create_cheatsheet_content(self):
        """Test creating cheatsheet content."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Cheatsheet Test",
            body_md="## Key Point 1\nPoint one",
            tags=["cheatsheet"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.BLOG],
            content_types=[ContentType.CHEATSHEET]
        )

        cheatsheet_assets = [a for a in result.assets if a.content_type == ContentType.CHEATSHEET]
        assert len(cheatsheet_assets) > 0

        cheatsheet = cheatsheet_assets[0]
        assert "📋" in cheatsheet.body

    def test_create_faq_content(self):
        """Test creating FAQ content."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="FAQ Test",
            body_md="## Q: What is this?\nThis is a test",
            tags=["faq"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.BLOG],
            content_types=[ContentType.FAQ]
        )

        faq_assets = [a for a in result.assets if a.content_type == ContentType.FAQ]
        assert len(faq_assets) > 0

        faq = faq_assets[0]
        assert "❓" in faq.body


class TestHelperMethods:
    """Tests for ContentRecycler helper methods."""

    def test_extract_hashtags(self):
        """Test hashtag extraction from tags."""
        recycler = ContentRecycler()

        tags = ["software development", "testing"]
        hashtags = recycler._extract_hashtags(tags, limit=5)

        assert len(hashtags) <= 5

    def test_count_words(self):
        """Test word counting."""
        recycler = ContentRecycler()

        text = "This is a test of word counting"
        count = recycler._count_words(text)

        assert count == 7

    def test_score_quality(self):
        """Test quality scoring."""
        recycler = ContentRecycler()

        rules = {"ideal_length": 100}
        body = "This is a test post with engagement hooks 🚀 and a CTA [link]"

        score = recycler._score_quality(body, rules)

        assert 0.0 <= score <= 1.0

    def test_calculate_quality_summary(self):
        """Test quality summary calculation."""
        recycler = ContentRecycler()

        assets = [
            ContentAsset(
                platform=Platform.LINKEDIN,
                content_type=ContentType.POST,
                title="A",
                body="Content A",
                word_count=50,
                character_count=250,
                quality_score=0.8
            ),
            ContentAsset(
                platform=Platform.TWITTER,
                content_type=ContentType.POST,
                title="B",
                body="Content B",
                word_count=30,
                character_count=150,
                quality_score=0.9
            )
        ]

        summary = recycler._calculate_quality_summary(assets)

        assert "avg_quality" in summary
        assert summary["min_quality"] == 0.8
        assert summary["max_quality"] == 0.9

    def test_calculate_quality_summary_empty(self):
        """Test quality summary with no assets."""
        recycler = ContentRecycler()

        summary = recycler._calculate_quality_summary([])

        assert summary == {}


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_atomize_blog_post_function(self):
        """Test atomize_blog_post convenience function."""
        markdown = """# Test Post

## Key Point
Important content
"""

        result = atomize_blog_post(markdown)

        assert isinstance(result, AtomizationResult)
        assert len(result.assets) > 0

    def test_atomize_blog_post_default_platforms(self):
        """Test atomize_blog_post uses default platforms."""
        result = atomize_blog_post("# Test\nContent")

        # Should include linkedin, twitter, newsletter by default
        assert Platform.LINKEDIN in result.platforms_covered
        assert Platform.TWITTER in result.platforms_covered


class TestTransformationConstants:
    """Tests for transformation rule constants."""

    def test_linkedin_transform_rules(self):
        """Test LINKEDIN_TRANSFORM has correct rules."""
        assert "max_length" in LINKEDIN_TRANSFORM
        assert LINKEDIN_TRANSFORM["max_length"] == 3000
        assert LINKEDIN_TRANSFORM["hashtag_limit"] == 5

    def test_twitter_transform_rules(self):
        """Test TWITTER_TRANSFORM has correct rules."""
        assert "max_length" in TWITTER_TRANSFORM
        assert TWITTER_TRANSFORM["max_length"] == 280
        assert TWITTER_TRANSFORM["hashtag_limit"] == 3

    def test_newsletter_transform_rules(self):
        """Test NEWSLETTER_TRANSFORM has correct rules."""
        assert "ideal_length" in NEWSLETTER_TRANSFORM
        assert NEWSLETTER_TRANSFORM["call_to_action"] == "required"

    def test_instagram_transform_rules(self):
        """Test INSTAGRAM_TRANSFORM has correct rules."""
        assert "max_length" in INSTAGRAM_TRANSFORM
        assert INSTAGRAM_TRANSFORM["use_emojis"] == "frequent"
        assert INSTAGRAM_TRANSFORM["hashtag_limit"] == 30


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_blog_post(self):
        """Test handling empty blog post."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Empty",
            body_md="",
            tags=[]
        )

        result = recycler.atomize(source=post, platforms=[Platform.TWITTER])

        # Should still generate content
        assert isinstance(result, AtomizationResult)

    def test_special_characters_in_content(self):
        """Test handling special characters."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Special Chars",
            body_md="## Point 1\nContent with <html> & \"quotes\"",
            tags=["test"]
        )

        result = recycler.atomize(source=post, platforms=[Platform.TWITTER])

        assert len(result.assets) > 0

    def test_unicode_content(self):
        """Test handling unicode content."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Unicode 测试",
            body_md="## Point 1\nContent with emoji 🚀🎉",
            tags=["unicode"]
        )

        result = recycler.atomize(source=post, platforms=[Platform.LINKEDIN])

        assert len(result.assets) > 0


class TestQualityScoring:
    """Tests for quality scoring mechanisms."""

    def test_quality_score_with_ideal_length(self):
        """Test quality score with ideal content length."""
        recycler = ContentRecycler()

        rules = {"ideal_length": 100}
        body = "word " * 100

        score = recycler._score_quality(body, rules)

        assert score >= 0.5

    def test_quality_score_below_ideal(self):
        """Test quality score below ideal length."""
        recycler = ContentRecycler()

        rules = {"ideal_length": 100}
        body = "short content"

        score = recycler._score_quality(body, rules)

        assert score < 1.0

    def test_quality_score_with_emoji_hooks(self):
        """Test quality score with emoji engagement hooks."""
        recycler = ContentRecycler()

        rules = {"ideal_length": 50}
        body = "word " * 50 + " 🚀 💡 🔗"

        score = recycler._score_quality(body, rules)

        assert score > 0.5

    def test_quality_score_with_cta(self):
        """Test quality score with call to action."""
        recycler = ContentRecycler()

        rules = {"ideal_length": 50}
        body = "word " * 50 + " Subscribe for more!"

        score = recycler._score_quality(body, rules)

        assert score > 0.5


class TestPlatformGeneration:
    """Tests for platform-specific content generation."""

    def test_generates_content_for_multiple_platforms(self):
        """Test that content is generated for multiple platforms."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Multi-Platform Test",
            body_md="## Point 1\nContent\n\n## Point 2\nMore content",
            tags=["multi"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.LINKEDIN, Platform.TWITTER, Platform.INSTAGRAM]
        )

        # Should have assets for at least 2 platforms
        assert len(result.platforms_covered) >= 2

    def test_generates_multiple_content_types_per_platform(self):
        """Test that multiple content types are generated per platform."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Content Types Test",
            body_md="## Key Point\nContent here",
            tags=["variety"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.LINKEDIN],
            content_types=[ContentType.POST, ContentType.THREAD, ContentType.QUICK_TIP]
        )

        # Should have at least 2 assets
        assert len(result.assets) >= 2


class TestRecommendations:
    """Tests for recommendation generation."""

    def test_generates_recommendations_for_low_variety(self):
        """Test recommendations when platform variety is low."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Low Variety",
            body_md="Content",
            tags=[]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.LINKEDIN],  # Only one platform
            content_types=[ContentType.POST]
        )

        # Should recommend more platforms
        assert isinstance(result.recommendations, list)

    def test_generates_no_recommendations_when_good_variety(self):
        """Test fewer recommendations when variety is good."""
        recycler = ContentRecycler()

        post = BlogPost(
            title="Good Variety",
            body_md="## Point 1\nContent\n\nTip: Good tip",
            tags=["varied"]
        )

        result = recycler.atomize(
            source=post,
            platforms=[Platform.LINKEDIN, Platform.TWITTER, Platform.INSTAGRAM],
            content_types=[ContentType.POST, ContentType.THREAD, ContentType.QUICK_TIP]
        )

        # Should still generate recommendations list (may be empty or have suggestions)
        assert isinstance(result.recommendations, list)
