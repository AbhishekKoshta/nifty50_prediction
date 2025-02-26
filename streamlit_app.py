import streamlit as st
import pandas as pd

# Load the data
@st.cache_data
def load_data():
    data = pd.read_csv('Nifty_Features.csv')
    return data

data = load_data()

# Title of the dashboard
st.title("Nifty Market Analysis Dashboard")

# Sidebar for user inputs
st.sidebar.header("User Input Features")

# Display the raw data
if st.sidebar.checkbox("Show raw data"):
    st.subheader("Raw Data")
    st.write(data)

# Probability of market opening gap up/gap down/neutral
st.subheader("Probability of Market Opening Gap")

# Calculate probabilities
total_days = len(data)
gap_up_days = len(data[data['opening_category'] == 'Gap Up'])
gap_down_days = len(data[data['opening_category'] == 'Gap Down'])
neutral_days = len(data[data['opening_category'] == 'Flat'])

prob_gap_up = gap_up_days / total_days
prob_gap_down = gap_down_days / total_days
prob_neutral = neutral_days / total_days

# Display probabilities
st.write(f"Probability of Gap Up: {prob_gap_up:.2%}")
st.write(f"Probability of Gap Down: {prob_gap_down:.2%}")
st.write(f"Probability of Neutral Opening: {prob_neutral:.2%}")

# Probability that market will close in green or red
st.subheader("Probability of Market Closing in Green or Red")

# Calculate probabilities
green_days = len(data[data['candle_color'] == 'Green'])
red_days = len(data[data['candle_color'] == 'Red'])

prob_green = green_days / total_days
prob_red = red_days / total_days

# Display probabilities
st.write(f"Probability of Closing in Green: {prob_green:.2%}")
st.write(f"Probability of Closing in Red: {prob_red:.2%}")

# Additional analysis: Probability of closing in green given a gap up
st.subheader("Probability of Closing in Green Given a Gap Up")

green_given_gap_up = len(data[(data['opening_category'] == 'Gap Up') & (data['candle_color'] == 'Green')]) / gap_up_days
st.write(f"Probability of Closing in Green Given a Gap Up: {green_given_gap_up:.2%}")

# Additional analysis: Probability of closing in red given a gap down
st.subheader("Probability of Closing in Red Given a Gap Down")

red_given_gap_down = len(data[(data['opening_category'] == 'Gap Down') & (data['candle_color'] == 'Red')]) / gap_down_days
st.write(f"Probability of Closing in Red Given a Gap Down: {red_given_gap_down:.2%}")

# Additional analysis: Probability of closing in green given a neutral opening
st.subheader("Probability of Closing in Green Given a Neutral Opening")

green_given_neutral = len(data[(data['opening_category'] == 'Flat') & (data['candle_color'] == 'Green')]) / neutral_days
st.write(f"Probability of Closing in Green Given a Neutral Opening: {green_given_neutral:.2%}")

# Additional analysis: Probability of closing in red given a neutral opening
st.subheader("Probability of Closing in Red Given a Neutral Opening")

red_given_neutral = len(data[(data['opening_category'] == 'Flat') & (data['candle_color'] == 'Red')]) / neutral_days
st.write(f"Probability of Closing in Red Given a Neutral Opening: {red_given_neutral:.2%}")
