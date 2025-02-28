import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
# Load the data
@st.cache_data
def load_data():
    tmp = pd.read_csv('Nifty_Features.csv')
    tmp = tmp.sort_values(by='date')
    return tmp

data = load_data()

# Convert date column to datetime
data['date'] = pd.to_datetime(data['date'])

# Sidebar for date range selection
st.sidebar.header('Select Date Range')
start_date = st.sidebar.date_input('Start date', data['date'].min())
end_date = st.sidebar.date_input('End date', data['date'].max())

# Filter data based on selected date range
filtered_data = data[(data['date'] >= pd.to_datetime(start_date)) & (data['date'] <= pd.to_datetime(end_date))]

# Dashboard title
st.title('Nifty50 Market Analysis Dashboard')

# Probability of market opening gap up/gap down/neutral
st.header('Probability of Market Opening Type')
opening_counts = filtered_data['opening_category'].value_counts(normalize=True)
st.bar_chart(opening_counts)

# Display the probabilities
st.write("Probabilities:")
st.write(opening_counts)

# Probability that market will close in green or red
st.header('Probability of Market Closing in Green or Red')
closing_color_counts = filtered_data['candle_color'].value_counts(normalize=True)
st.bar_chart(closing_color_counts)

# # Display the probabilities
# st.write("Gray- Market closed within 0.2% (abs) from opening\n")
st.write("Probabilities:")
st.write(closing_color_counts)
st.markdown('''<p style="font-size: 12px;">Gray- Market closed within 0.2% (absolute) from opening</p>''', unsafe_allow_html=True)

# Probability that market will give exceptional move
st.header('Probability of Market giving an Exceptional Move')
move_category_counts = filtered_data['move_category'].value_counts(normalize=True)
st.bar_chart(move_category_counts)

# Display the probabilities
st.write("Probabilities:")
st.write(move_category_counts)
st.markdown('''
<p style="font-size: 12px;">
Low: absolute percentage (pct) is less than 0.5 <br>
Moderate: absolute percentage is between 0.5 (inclusive) and 1.0 (exclusive) <br>
High: absolute percentage is between 1.0 (inclusive) and 1.5 (exclusive) <br>
Exceptional: absolute percentage is 1.5 or higher
</p>
''', unsafe_allow_html=True)


# Additional visualizations
st.header('Additional Visualizations')

# Plot of percentage move in a day
st.subheader('Percentage Move in a Day')
fig, ax = plt.subplots()
sns.histplot(filtered_data['abs_directional_move_pct'], kde=True, ax=ax)
ax.set_xlabel('Percentage Move in a Day')
ax.set_ylabel('Frequency')
st.pyplot(fig)

# Plot of percentage move in 3 days
st.subheader('Percentage Move in 3 Days')
fig, ax = plt.subplots()
sns.histplot(filtered_data['pct_move_3d'], kde=True, ax=ax)
ax.set_xlabel('Percentage Move in 3 Days')
ax.set_ylabel('Frequency')
st.pyplot(fig)

# Plot of percentage move in 5 days
st.subheader('Percentage Move in 5 Days')
fig, ax = plt.subplots()
sns.histplot(filtered_data['pct_move_5d'], kde=True, ax=ax)
ax.set_xlabel('Percentage Move in 5 Days')
ax.set_ylabel('Frequency')
st.pyplot(fig)

# Display the filtered data
st.header('Filtered Data')
st.write(filtered_data)
